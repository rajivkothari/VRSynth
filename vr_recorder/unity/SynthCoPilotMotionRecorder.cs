// SynthCoPilotMotionRecorder.cs
//
// First-draft Unity recorder for SynthCoPilot VR choreography capture.
// See vr_recorder/UNITY_RECORDER_SPEC.md. This is a concrete starting point, not
// a hardware-validated final build:
//   * Sampling shares the main thread; JSON is written on stop.
//   * The Unity(LH) -> format(RH) handedness conversion is marked TODO: verify
//     against tools/visualize_motion.py before trusting orientation.
//   * Audio latency is NOT compensated (song time = audioSource.time).
//
// Setup: drop on a "Recorder" GameObject, assign the HMD / left / right
// transforms (from the XR Origin) and an AudioSource with the song clip.
// Output: a MotionRecording JSON in Application.persistentDataPath, loadable by
// synthcopilot/motion.py (passes validate_motion_recording).

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

namespace VRSynth
{
    public class SynthCoPilotMotionRecorder : MonoBehaviour
    {
        [Header("Tracked transforms (from the XR Origin)")]
        public Transform headset;
        public Transform leftController;
        public Transform rightController;

        [Header("Audio")]
        public AudioSource audioSource;
        public string songPath = "song.mp3";   // recorded into the JSON as song_path

        [Header("Tempo (optional; 0 = unknown -> null in JSON)")]
        public float bpm = 0f;
        public float offset = 0f;               // song offset in seconds (0 until calibrated)

        [Header("Capture")]
        [Tooltip("Pose samples per second.")]
        public float sampleRateHz = 72f;
        [Tooltip("Convert Unity left-handed -> format right-handed (Y-up). TODO: verify.")]
        public bool convertToRightHanded = true;

        [Header("Controls (keyboard fallback; bind to controller buttons in Input System)")]
        public KeyCode toggleKey = KeyCode.Space;
        public KeyCode calibrateKey = KeyCode.C;

        [Serializable]
        private struct DeviceSample
        {
            public Vector3 position;
            public Quaternion rotation;
            public Vector3 velocity;
            public bool hasVelocity;
        }

        private struct Frame
        {
            public double t;
            public DeviceSample head;
            public DeviceSample left;
            public DeviceSample right;
        }

        private readonly List<Frame> _frames = new List<Frame>();
        private bool _recording;
        private Coroutine _loop;

        // Previous positions/time for finite-difference velocity (in target space).
        private Vector3 _prevHead, _prevLeft, _prevRight;
        private double _prevSampleTime;
        private bool _havePrev;

        // Calibration captured before a take.
        private bool _calibrated;
        private float _hmdHeight;
        private Vector3 _leftRest, _rightRest;

        private void Update()
        {
            if (Input.GetKeyDown(calibrateKey)) Calibrate();
            if (Input.GetKeyDown(toggleKey)) ToggleRecording();
        }

        public void ToggleRecording()
        {
            if (_recording) StopRecording();
            else StartRecording();
        }

        public void Calibrate()
        {
            if (headset == null || leftController == null || rightController == null)
            {
                Debug.LogWarning("[Recorder] Assign HMD/left/right transforms before calibrating.");
                return;
            }
            _hmdHeight = headset.position.y;
            _leftRest = ToTargetSpace(leftController.position);
            _rightRest = ToTargetSpace(rightController.position);
            _calibrated = true;
            Debug.Log($"[Recorder] Calibrated. HMD height={_hmdHeight:F2}m");
        }

        public void StartRecording()
        {
            if (_recording) return;
            if (headset == null || leftController == null || rightController == null)
            {
                Debug.LogError("[Recorder] Missing tracked transforms; cannot record.");
                return;
            }
            _frames.Clear();
            _havePrev = false;
            _recording = true;
            if (audioSource != null) audioSource.Play();
            _loop = StartCoroutine(SampleLoop());
            Debug.Log("[Recorder] ● REC");
        }

        public void StopRecording()
        {
            if (!_recording) return;
            _recording = false;
            if (_loop != null) StopCoroutine(_loop);
            if (audioSource != null) audioSource.Stop();
            string path = WriteJson();
            Debug.Log($"[Recorder] Stopped. Wrote {_frames.Count} frames to {path}");
        }

        // Fixed-interval sampler decoupled from frame rate via an accumulator.
        private IEnumerator SampleLoop()
        {
            float interval = 1f / Mathf.Max(1f, sampleRateHz);
            double next = SongTime();
            while (_recording)
            {
                double now = SongTime();
                if (now + 1e-6 >= next)
                {
                    CaptureFrame(now);
                    next += interval;
                    // If we fell behind, don't spiral; resync to now.
                    if (now > next) next = now + interval;
                }
                yield return null;
            }
        }

        private double SongTime()
        {
            if (audioSource != null && audioSource.isPlaying) return audioSource.time;
            return Time.realtimeSinceStartupAsDouble; // fallback if no audio assigned
        }

        private void CaptureFrame(double t)
        {
            Vector3 hPos = ToTargetSpace(headset.position);
            Vector3 lPos = ToTargetSpace(leftController.position);
            Vector3 rPos = ToTargetSpace(rightController.position);

            double dt = _havePrev ? (t - _prevSampleTime) : 0.0;
            bool hasVel = _havePrev && dt > 0.0;

            var frame = new Frame
            {
                t = t,
                head = MakeSample(hPos, headset.rotation, _prevHead, dt, hasVel),
                left = MakeSample(lPos, leftController.rotation, _prevLeft, dt, hasVel),
                right = MakeSample(rPos, rightController.rotation, _prevRight, dt, hasVel),
            };
            _frames.Add(frame);

            _prevHead = hPos; _prevLeft = lPos; _prevRight = rPos;
            _prevSampleTime = t;
            _havePrev = true;
        }

        private DeviceSample MakeSample(Vector3 pos, Quaternion rot, Vector3 prevPos, double dt, bool hasVel)
        {
            var s = new DeviceSample
            {
                position = pos,
                rotation = ToTargetSpace(rot),
                hasVelocity = hasVel,
            };
            if (hasVel) s.velocity = (pos - prevPos) / (float)dt;
            return s;
        }

        // --- Unity (left-handed, Y-up) -> format (right-handed, Y-up) ---
        // TODO: VERIFY this convention by replaying output in tools/visualize_motion.py.
        // The common choice is to negate Z on position and mirror x/y of the quaternion.
        private Vector3 ToTargetSpace(Vector3 p)
        {
            return convertToRightHanded ? new Vector3(p.x, p.y, -p.z) : p;
        }

        private Quaternion ToTargetSpace(Quaternion q)
        {
            return convertToRightHanded ? new Quaternion(-q.x, -q.y, q.z, q.w) : q;
        }

        // --- JSON writing (schema matches synthcopilot/motion.py) ---
        private string WriteJson()
        {
            string fileName = BuildFileName();
            string path = Path.Combine(Application.persistentDataPath, fileName);
            float sr = EstimateSampleRate();

            var sb = new StringBuilder();
            sb.Append("{\n");
            sb.Append("  \"format_version\": \"0.1.0\",\n");
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"song_path\": \"{0}\",\n", Escape(songPath));
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"sample_rate\": {0},\n", F(sr));
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"bpm\": {0},\n", bpm > 0f ? F(bpm) : "null");
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"offset\": {0},\n", F(offset));
            sb.Append("  \"metadata\": {");
            sb.AppendFormat(CultureInfo.InvariantCulture, "\"recorder\": \"unity_openxr\", \"unity_version\": \"{0}\", ", Application.unityVersion);
            sb.AppendFormat(CultureInfo.InvariantCulture, "\"handedness_converted\": {0}", convertToRightHanded ? "true" : "false");
            if (_calibrated)
            {
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    ", \"calibration\": {{\"hmd_height_m\": {0}, \"left_rest\": [{1},{2},{3}], \"right_rest\": [{4},{5},{6}]}}",
                    F(_hmdHeight), F(_leftRest.x), F(_leftRest.y), F(_leftRest.z),
                    F(_rightRest.x), F(_rightRest.y), F(_rightRest.z));
            }
            sb.Append("},\n");

            sb.Append("  \"frames\": [\n");
            for (int i = 0; i < _frames.Count; i++)
            {
                sb.Append(FrameJson(_frames[i]));
                sb.Append(i < _frames.Count - 1 ? ",\n" : "\n");
            }
            sb.Append("  ]\n}\n");

            File.WriteAllText(path, sb.ToString());
            return path;
        }

        private string FrameJson(Frame f)
        {
            return "    {"
                + "\"time_seconds\": " + F(f.t) + ", "
                + "\"headset\": " + PoseJson(f.t, f.head) + ", "
                + "\"left_controller\": " + PoseJson(f.t, f.left) + ", "
                + "\"right_controller\": " + PoseJson(f.t, f.right)
                + "}";
        }

        private string PoseJson(double t, DeviceSample d)
        {
            var sb = new StringBuilder();
            sb.Append("{");
            sb.AppendFormat(CultureInfo.InvariantCulture, "\"time_seconds\": {0}, ", F(t));
            sb.AppendFormat(CultureInfo.InvariantCulture,
                "\"position_x\": {0}, \"position_y\": {1}, \"position_z\": {2}, ",
                F(d.position.x), F(d.position.y), F(d.position.z));
            sb.AppendFormat(CultureInfo.InvariantCulture,
                "\"rotation_x\": {0}, \"rotation_y\": {1}, \"rotation_z\": {2}, \"rotation_w\": {3}",
                F(d.rotation.x), F(d.rotation.y), F(d.rotation.z), F(d.rotation.w));
            if (d.hasVelocity)
            {
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    ", \"velocity_x\": {0}, \"velocity_y\": {1}, \"velocity_z\": {2}",
                    F(d.velocity.x), F(d.velocity.y), F(d.velocity.z));
            }
            sb.Append("}");
            return sb.ToString();
        }

        private float EstimateSampleRate()
        {
            if (_frames.Count < 2) return sampleRateHz;
            double span = _frames[_frames.Count - 1].t - _frames[0].t;
            return span > 0 ? (float)((_frames.Count - 1) / span) : sampleRateHz;
        }

        private string BuildFileName()
        {
            string song = Path.GetFileNameWithoutExtension(songPath);
            var clean = new StringBuilder();
            foreach (char c in song)
                clean.Append(char.IsLetterOrDigit(c) ? c : '-');
            string stamp = DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture);
            return $"{clean}_take_{stamp}.motion.json";
        }

        private static string F(double v) => v.ToString("R", CultureInfo.InvariantCulture);
        private static string Escape(string s) => s.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }
}
