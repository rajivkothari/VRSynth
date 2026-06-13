// SongClock.cs — single source of truth for song time.
//
// SCAFFOLDING: structure only; not run on hardware. Drop into a Unity OpenXR
// project (see ../README.md).
//
// Establishes t0 when the song starts so motion and audio share one timeline.
// NOTE: audio output latency is NOT compensated here (Phase 2 work).

using UnityEngine;

namespace VRSynth.Recorder
{
    public class SongClock : MonoBehaviour
    {
        public AudioSource song;

        private bool _started;
        private double _t0DspTime;

        // Start the song and latch t0 to the audio DSP clock (most stable).
        public void StartSong()
        {
            _t0DspTime = AudioSettings.dspTime;
            if (song != null) song.Play();
            _started = true;
        }

        // Current song time in seconds (0 at song start). Frames stamp this.
        public double SongTimeSeconds()
        {
            if (!_started) return 0.0;
            return AudioSettings.dspTime - _t0DspTime; // TODO: subtract measured output latency
        }

        public bool IsStarted => _started;
    }
}
