#ifndef PYMERGETIC_RXF_DELTA_EDIT_HPP
#define PYMERGETIC_RXF_DELTA_EDIT_HPP

/*
 * The edit set (docs/27): one mutation is one delta. The verbs are the typed face of the one
 * delta vocabulary; each builds operations and replays them through apply, so the refusals of
 * the edit are the refusals of the delta and of the link check — there is no second path into
 * the image, and the text form (.rxfd) is the same ops written down.
 */

#include <cstdint>
#include <string>

#include <pymergetic/rxftk/core/delta/delta.hpp>
#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::delta {

using object::Image;
using object::Status;

/* One mutation, applied now and atomically. `create` answers the handle of the new cell (size
 * and align come from its Type); `ref` is the only place a handle may be written; `unset` is
 * for OPTIONAL fields only. A derived value and a declared list take no value and no place:
 * the refusal comes before the delta exists, as one of the link check's codes. */
Status edit_create(Image &image, pymergetic_rxf_handle type, pymergetic_rxf_handle parent,
                   const std::string &name, pymergetic_rxf_handle *out, std::string *error);
Status edit_rename(Image &image, pymergetic_rxf_handle target, const std::string &name,
                   std::string *error);
Status edit_move(Image &image, pymergetic_rxf_handle target, pymergetic_rxf_handle parent,
                 std::string *error);
Status edit_delete(Image &image, pymergetic_rxf_handle target, std::string *error);
Status edit_put(Image &image, pymergetic_rxf_handle target, const std::string &field,
                const std::string &value, std::string *error);
Status edit_ref(Image &image, pymergetic_rxf_handle target, const std::string &field,
                pymergetic_rxf_handle to, std::string *error);
Status edit_unset(Image &image, pymergetic_rxf_handle target, const std::string &field,
                  std::string *error);

/* The transaction (docs/27): the verbs collect operations in the currency of the text form —
 * paths, never handles — and commit is ONE delta: all of it or none of it, written down as one
 * .rxfd (the history entry of the delta chain). Until commit the image does not change;
 * rollback drops the operations. */
class Editor {
  public:
    explicit Editor(Image &image) : image_(&image) {
    }
    Editor(const Editor &) = delete;
    Editor &operator=(const Editor &) = delete;
    Editor(Editor &&) = default;
    Editor &operator=(Editor &&) = default;

    std::string create(const std::string &type, const std::string &parent,
                       const std::string &name); // its path
    void rename(const std::string &path, const std::string &name);
    void move(const std::string &path, const std::string &parent);
    void remove(const std::string &path);
    void put(const std::string &path, const std::string &field, const std::string &value);
    void ref(const std::string &path, const std::string &field, const std::string &target);
    void unset(const std::string &path, const std::string &field);
    void entry(const std::string &path);

    /* ONE delta: the operations replay as a whole — all of it or none of it — and the .rxfd
     * that spells them is the answer. */
    Status commit(std::string *delta_text, std::string *error);
    void rollback();

    bool open() const {
        return open_;
    }
    size_t size() const {
        return delta_.ops.size();
    }
    const Delta &delta() const {
        return delta_;
    }

  private:
    Image *image_;
    Delta delta_;
    bool open_ = true;
};

} // namespace pymergetic::rxf::core::delta

#endif
