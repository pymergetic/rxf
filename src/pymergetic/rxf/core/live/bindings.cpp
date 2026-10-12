#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxf/core/bindings.hpp>
#include <pymergetic/rxf/core/live/live.hpp>

#include <cstring>
#include <memory>
#include <vector>

namespace pymergetic::rxf::core::bindings {

namespace {

/* One block of host memory laid out as the live area of a linked image would be: the area struct,
 * then arena, log, stage, work, emit, and the two rings. The host tests run the very code of the
 * native leaf on it. */
class LiveArea {
public:
    static uint64_t up(uint64_t v) { return (v + 4095) & ~uint64_t(4095); }

    /* The bytes a block with these capacities takes: the area, the regions, the rings. */
    static uint64_t required(uint64_t arena, uint64_t log, uint64_t stage, uint64_t work, uint64_t emit, uint64_t ring) {
        return 4096 + up(arena) + up(log) + up(stage) + up(work) + up(emit) + 2 * ring;
    }

    /* A block of memory of the process. */
    LiveArea(const nb::bytes &image, uint64_t arena_capacity, uint64_t log_capacity, uint64_t stage_capacity, uint64_t work_capacity,
             uint64_t emit_capacity, uint64_t ring_capacity, uint64_t table_slots, uint64_t arena_committed) {
        memory_.assign((required(arena_capacity, log_capacity, stage_capacity, work_capacity, emit_capacity, ring_capacity) + 7) / 8, 0);
        lay_out(reinterpret_cast<uint8_t *>(memory_.data()), image, arena_capacity, log_capacity, stage_capacity, work_capacity, emit_capacity,
                ring_capacity, table_slots, arena_committed);
    }

    /* Memory another process can see (a shared-memory segment), which is laid out in place. */
    LiveArea(const nb::object &memory, const nb::bytes &image, uint64_t arena_capacity, uint64_t log_capacity, uint64_t stage_capacity,
             uint64_t work_capacity, uint64_t emit_capacity, uint64_t ring_capacity, uint64_t table_slots, uint64_t arena_committed) {
        take(memory);
        if (static_cast<uint64_t>(view_.len) < required(arena_capacity, log_capacity, stage_capacity, work_capacity, emit_capacity, ring_capacity))
            throw ArenaError("the memory is too small for the area");
        std::memset(view_.buf, 0, static_cast<size_t>(view_.len));
        lay_out(static_cast<uint8_t *>(view_.buf), image, arena_capacity, log_capacity, stage_capacity, work_capacity, emit_capacity, ring_capacity,
                table_slots, arena_committed);
    }

    /* The same area, seen from another process: its addresses are made the ones of this mapping. */
    explicit LiveArea(const nb::object &memory) {
        take(memory);
        base_ = static_cast<uint8_t *>(view_.buf);
        area_ = reinterpret_cast<pymergetic_rxf_live_area *>(base_);
        if (area_->magic != PYMERGETIC_RXF_LIVE_MAGIC || area_->version != PYMERGETIC_RXF_LIVE_VERSION) throw ArenaError("the memory holds no live area");
        const uint64_t shift = reinterpret_cast<uintptr_t>(base_) - (area_->arena_base - 4096);
        uint64_t *const fields[] = {&area_->arena_base, &area_->log_base, &area_->stage_base, &area_->work_base, &area_->emit_base, &area_->rx_base, &area_->tx_base};
        for (uint64_t *field : fields) *field += shift;
        ring_ = area_->rx_capacity;
    }

    ~LiveArea() {
        if (has_view_) PyBuffer_Release(&view_);
    }
    LiveArea(const LiveArea &) = delete;
    LiveArea &operator=(const LiveArea &) = delete;

private:
    void take(const nb::object &memory) {
        if (PyObject_GetBuffer(memory.ptr(), &view_, PyBUF_WRITABLE | PyBUF_SIMPLE) != 0) throw nb::python_error();
        has_view_ = true;
    }

    void lay_out(uint8_t *memory, const nb::bytes &image, uint64_t arena_capacity, uint64_t log_capacity, uint64_t stage_capacity,
                 uint64_t work_capacity, uint64_t emit_capacity, uint64_t ring_capacity, uint64_t table_slots, uint64_t arena_committed) {
        if (image.size() > arena_capacity) throw ArenaError("the image does not fit the arena");
        if ((ring_capacity & (ring_capacity - 1)) != 0 || (table_slots & (table_slots - 1)) != 0) throw ArenaError("capacities of rings and table are powers of two");
        arena_ = up(arena_capacity);
        log_ = up(log_capacity);
        stage_ = up(stage_capacity);
        work_ = up(work_capacity);
        emit_ = up(emit_capacity);
        ring_ = ring_capacity;
        base_ = memory;
        area_ = reinterpret_cast<pymergetic_rxf_live_area *>(base_);
        uint64_t at = 4096;
        const auto place = [&](uint64_t size) {
            const uint64_t where = reinterpret_cast<uintptr_t>(base_) + at;
            at += size;
            return where;
        };
        area_->magic = PYMERGETIC_RXF_LIVE_MAGIC;
        area_->version = PYMERGETIC_RXF_LIVE_VERSION;
        area_->arena_base = place(arena_);
        area_->arena_capacity = arena_;
        if (arena_committed != 0 && (arena_committed < image.size() || arena_committed > arena_)) throw ArenaError("committed is between the image and the arena");
        area_->arena_committed = arena_committed;
        area_->log_base = place(log_);
        area_->log_capacity = log_;
        area_->stage_base = place(stage_);
        area_->stage_capacity = stage_;
        area_->work_base = place(work_);
        area_->work_capacity = work_;
        area_->emit_base = place(emit_);
        area_->emit_capacity = emit_;
        area_->rx_base = place(ring_);
        area_->rx_capacity = ring_;
        area_->tx_base = place(ring_);
        area_->tx_capacity = ring_;
        area_->table_slots = static_cast<uint32_t>(table_slots);
        std::memcpy(reinterpret_cast<void *>(area_->arena_base), image.c_str(), image.size());
    }

public:

    uint64_t rx_write(const nb::bytes &data) {
        const uint32_t used = area_->rx_head - area_->rx_tail;
        if (data.size() > ring_ - used) return 0;
        uint8_t *rx = reinterpret_cast<uint8_t *>(area_->rx_base);
        for (size_t i = 0; i < data.size(); i++) rx[(area_->rx_head + i) & (ring_ - 1)] = static_cast<uint8_t>(data.c_str()[i]);
        area_->rx_head += static_cast<uint32_t>(data.size());
        return data.size();
    }

    nb::bytes tx_read(uint64_t limit) {
        const uint32_t used = area_->tx_head - area_->tx_tail;
        const uint64_t count = std::min<uint64_t>(used, limit);
        std::vector<char> out(count);
        const uint8_t *tx = reinterpret_cast<const uint8_t *>(area_->tx_base);
        for (uint64_t i = 0; i < count; i++) out[i] = static_cast<char>(tx[(area_->tx_tail + i) & (ring_ - 1)]);
        area_->tx_tail += static_cast<uint32_t>(count);
        return nb::bytes(out.data(), out.size());
    }

    uint32_t pump(uint32_t limit) { return live::pump(area_, limit); }

    nb::bytes arena_bytes() const {
        const auto *h = reinterpret_cast<const pymergetic_rxf_arena_header *>(area_->arena_base);
        return nb::bytes(reinterpret_cast<const char *>(area_->arena_base), h->body_size);
    }

    nb::bytes emit_bytes() const { return nb::bytes(reinterpret_cast<const char *>(area_->emit_base), area_->emit_length); }
    nb::bytes digest() const { return nb::bytes(reinterpret_cast<const char *>(area_->emit_digest), 32); }
    pymergetic_rxf_live_area *area() { return area_; }

private:
    std::vector<uint64_t> memory_;
    Py_buffer view_{};
    bool has_view_ = false;
    uint8_t *base_ = nullptr;
    pymergetic_rxf_live_area *area_ = nullptr;
    uint64_t arena_ = 0, log_ = 0, stage_ = 0, work_ = 0, emit_ = 0, ring_ = 0;
};

} // namespace

void bind_live(nb::module_ &m) {
    nb::class_<LiveArea>(m, "Area", "The live area of an image in host memory; `pump` runs the code of the native leaf.")
        .def(nb::init<const nb::bytes &, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t, uint64_t>(), nb::arg("image"),
             nb::arg("arena_capacity"), nb::arg("log_capacity"), nb::arg("stage_capacity"), nb::arg("work_capacity"), nb::arg("emit_capacity"),
             nb::arg("ring_capacity"), nb::arg("table_slots"), nb::arg("arena_committed") = 0)
        .def_static(
            "required_size", &LiveArea::required, nb::arg("arena_capacity"), nb::arg("log_capacity"), nb::arg("stage_capacity"), nb::arg("work_capacity"),
            nb::arg("emit_capacity"), nb::arg("ring_capacity"), "The bytes of memory an area with these capacities takes.")
        .def_static(
            "shared",
            [](const nb::object &memory, const nb::bytes &image, uint64_t a, uint64_t l, uint64_t st, uint64_t w, uint64_t e, uint64_t r, uint64_t t,
               uint64_t c) { return new LiveArea(memory, image, a, l, st, w, e, r, t, c); },
            nb::rv_policy::take_ownership, nb::arg("memory"), nb::arg("image"), nb::arg("arena_capacity"), nb::arg("log_capacity"),
            nb::arg("stage_capacity"), nb::arg("work_capacity"), nb::arg("emit_capacity"), nb::arg("ring_capacity"), nb::arg("table_slots"),
            nb::arg("arena_committed") = 0, "An area laid out in memory that another process can map (a shared-memory segment).")
        .def_static(
            "attach", [](const nb::object &memory) { return new LiveArea(memory); }, nb::rv_policy::take_ownership, nb::arg("memory"),
            "The area that `shared` laid out in this memory, seen from this process.")
        .def("rx_write", &LiveArea::rx_write, nb::arg("data"), "Appends bytes to the ring the image reads; 0 when they do not fit.")
        .def("tx_read", &LiveArea::tx_read, nb::arg("limit") = 1 << 20, "Takes up to `limit` bytes from the ring the image writes.")
        .def("pump", &LiveArea::pump, nb::arg("limit") = 64, "Serves up to `limit` requests; returns how many.")
        .def("arena_bytes", &LiveArea::arena_bytes, "The bytes of the arena, header to end of the cell stream.")
        .def("emit_bytes", &LiveArea::emit_bytes, "The last dump.")
        .def("digest", &LiveArea::digest, "The digest of the last dump.")
        .def_prop_ro("grow_request", [](LiveArea &s) { return s.area()->grow_request; })
        .def_prop_ro("arena_committed", [](LiveArea &s) { return s.area()->arena_committed; })
        .def_prop_ro("compactions", [](LiveArea &s) { return s.area()->compactions; })
        .def_prop_ro("frames", [](LiveArea &s) { return s.area()->frames; })
        .def_prop_ro("commits", [](LiveArea &s) { return s.area()->commits; })
        .def_prop_ro("rejects", [](LiveArea &s) { return s.area()->rejects; })
        .def_prop_ro("last_status", [](LiveArea &s) { return s.area()->last_status; })
        .def_prop_ro("stage_length", [](LiveArea &s) { return s.area()->stage_length; })
        .def_prop_rw(
            "break_request", [](LiveArea &s) { return s.area()->break_request; }, [](LiveArea &s, uint32_t v) { s.area()->break_request = v; })
        .def_prop_rw(
            "index_state", [](LiveArea &s) { return s.area()->index_state; }, [](LiveArea &s, uint32_t v) { s.area()->index_state = v; });
    m.def("crc32", [](const nb::bytes &data) { return live::crc32(data.c_str(), data.size()); }, nb::arg("data"), "CRC-32 (IEEE) as the frames use it.");
}

} // namespace pymergetic::rxf::core::bindings
