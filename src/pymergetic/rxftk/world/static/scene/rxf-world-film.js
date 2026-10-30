/* The world's film camera: one button in the HUD records a flight through the
 * city's named poses (rxf-world.js: CAMERA_POSES), played by the same public
 * face the README pins (RXFWorld.api.setPose). A flight is a sequence of pose
 * holds joined by the camera rig's own easing (Rig.step in the render loop);
 * the film is the canvas's own frames: canvas.captureStream + MediaRecorder,
 * webm — no second renderer, no re-encoding, the page's truth recorded.
 *
 * window.RXFWorldFilm = { record(flight?), state, supported }
 *
 * A flight (a default exists: the poses in CAMERA_POSES order) is
 *   { name: string, hold: seconds }[]
 * — a hold of 0 eases straight into the next pose. The recorder:
 *   record()  starts: fly each pose, hold it, then land on a download link
 *             (a blob URL, rxf-world-film.webm). While it runs the HUD button
 *             shows the elapsed time; a second click stops early and keeps
 *             what was flown so far.
 *   state     "idle" | "recording" | "unsupported"
 * The default flight covers every named pose: the tour the city deserves.
 */
(function (global) {
  "use strict";

  var HOLD_SECONDS = 2.2; /* how long each pose is held before easing on */
  var FPS = 30;

  function api() {
    return global.RXFWorld && global.RXFWorld.api;
  }

  function canvas() {
    return global.document && global.document.getElementById("rxf-world-canvas");
  }

  function supported() {
    return !!(global.MediaRecorder && canvas() && typeof canvas().captureStream === "function");
  }

  function flightFromPoses() {
    var poses = (api() && api().poses) || [];
    return poses.map(function (pose) {
      return { name: pose.name, hold: HOLD_SECONDS };
    });
  }

  function pickMime() {
    /* the honest pick: what this browser can actually record, in the order
     * the page prefers — never a name the browser would refuse */
    var candidates = [
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm"
    ];
    for (var i = 0; i < candidates.length; i++) {
      if (global.MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
    }
    return "";
  }

  var state = supported() ? "idle" : "unsupported";
  var recorder = null;
  var stopFlight = null;

  function sleep(seconds, aborted) {
    return new Promise(function (resolve) {
      var id = global.setTimeout(function () {
        global.clearInterval(watch);
        resolve(!aborted());
      }, seconds * 1000);
      var watch = global.setInterval(function () {
        if (aborted()) {
          global.clearTimeout(id);
          global.clearInterval(watch);
          resolve(false);
        }
      }, 50);
    });
  }

  async function fly(flight, onTick) {
    /* one flight through the city: each pose eased by the rig, then held.
     * Returns early (false) when the flight was aborted. */
    var current = api();
    if (!current) return false;
    var aborted = function () {
      return !recorder;
    };
    for (var i = 0; i < flight.length; i++) {
      if (aborted()) return false;
      /* instant=false eases the rig onto the pose through its own render loop */
      current.setPose(flight[i].name, false);
      var keep = await sleep(flight[i].hold, aborted);
      if (!keep) return false;
      if (onTick) onTick(i + 1, flight.length);
    }
    return true;
  }

  function record(flight) {
    var chosen = flight || flightFromPoses();
    if (state !== "idle") return Promise.resolve(null);
    if (!supported()) {
      state = "unsupported";
      return Promise.resolve(null);
    }
    var target = canvas();
    var stream = target.captureStream(FPS);
    var chunks = [];
    var mime = pickMime();
    recorder = new global.MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recorder.ondataavailable = function (event) {
      if (event.data && event.data.size) chunks.push(event.data);
    };
    var started = global.Date.now();
    state = "recording";
    return new Promise(function (resolve) {
      recorder.onstop = function () {
        stream.getTracks().forEach(function (track) {
          track.stop();
        });
        var blob = new global.Blob(chunks, { type: recorder.mimeType || "video/webm" });
        recorder = null;
        stopFlight = null;
        state = "idle";
        resolve({ blob: blob, seconds: (global.Date.now() - started) / 1000 });
      };
      recorder.start(250);
      fly(chosen, null).then(function () {
        if (recorder && recorder.state !== "inactive") recorder.stop();
      });
    });
  }

  function stop() {
    /* an early stop keeps what was flown so far — the recorder's onstop lands the film */
    if (stopFlight) stopFlight();
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }

  function download(film) {
    if (!film || !film.blob) return "";
    var url = global.URL.createObjectURL(film.blob);
    var a = global.document.createElement("a");
    a.href = url;
    a.download = "rxf-world-film.webm";
    a.textContent = `the flight (${Math.round(film.seconds)}s, webm)`;
    a.className = "film-link";
    /* the link lands beside the button: the film is the page's own output */
    var host = global.document.querySelector("[data-film-slot]");
    if (host) {
      host.textContent = "";
      host.appendChild(a);
    }
    return url;
  }

  global.RXFWorldFilm = {
    supported: supported,
    record: record,
    stop: stop,
    download: download,
    get state() {
      return state;
    }
  };
})(typeof window !== "undefined" ? window : this);