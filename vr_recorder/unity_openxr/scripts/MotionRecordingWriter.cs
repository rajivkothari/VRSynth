// MotionRecordingWriter.cs — serialize captured frames to MotionRecording JSON.
//
// SCAFFOLDING: structure only; NOT run on hardware. Drop into a Unity OpenXR
// project (see ../README.md).
//
// Emits the schema from synthcopilot/motion.py so the file loads with
// load_motion_recording and passes validate_motion_recording.
//
// IMPORTANT TODO — coordinate handedness: Unity is LEFT-handed (Y-up); our
// format is RIGHT-handed (Y-up). Convert before writing (negate Z on positions
// and mirror the quaternion accordingly) and verify against
// tools/visualize_motion.py. The hooks below are marked but intentionally NOT
// silently "done" — get this right on hardware.

using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

namespace VRSynth.Recorder
{
    public static class MotionRecordingWriter
    {
        private const string FormatVersion = "0.1.0";

        public static void Write(string path, string songPath, float bpm, List<CaptureFrame> frames)
        {
            float sampleRate = EstimateSampleRate(frames);
            var sb = new StringBuilder();
            sb.Append("{\n");
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"format_version\": \"{0}\",\n", FormatVersion);
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"song_path\": \"{0}\",\n", songPath);
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"sample_rate\": {0},\n", F(sampleRate));
            sb.AppendFormat(CultureInfo.InvariantCulture, "  \"bpm\": {0},\n", bpm > 0f ? F(bpm) : "null");
            sb.Append("  \"offset\": null,\n");
            sb.Append("  \"metadata\": {\"recorder\": \"unity_openxr\"},\n");
            sb.Append("  \"frames\": [\n");
            for (int i = 0; i < frames.Count; i++)
            {
                sb.Append(FrameJson(frames[i]));
                sb.Append(i < frames.Count - 1 ? ",\n" : "\n");
            }
            sb.Append("  ]\n}\n");
            File.WriteAllText(path, sb.ToString());
        }

        private static string FrameJson(CaptureFrame f)
        {
            string t = F(f.timeSeconds);
            return "    {"
                + "\"time_seconds\": " + t + ", "
                + "\"headset\": " + PoseJson(f.timeSeconds, f.head) + ", "
                + "\"left_controller\": " + PoseJson(f.timeSeconds, f.left) + ", "
                + "\"right_controller\": " + PoseJson(f.timeSeconds, f.right)
                + "}";
        }

        private static string PoseJson(double t, DeviceFrame d)
        {
            // TODO: apply Unity(LH) -> format(RH) conversion to position & rotation.
            Vector3 p = d.position;
            Quaternion q = d.rotation;
            var sb = new StringBuilder();
            sb.Append("{");
            sb.AppendFormat(CultureInfo.InvariantCulture, "\"time_seconds\": {0}, ", F(t));
            sb.AppendFormat(CultureInfo.InvariantCulture,
                "\"position_x\": {0}, \"position_y\": {1}, \"position_z\": {2}, ", F(p.x), F(p.y), F(p.z));
            sb.AppendFormat(CultureInfo.InvariantCulture,
                "\"rotation_x\": {0}, \"rotation_y\": {1}, \"rotation_z\": {2}, \"rotation_w\": {3}",
                F(q.x), F(q.y), F(q.z), F(q.w));
            if (d.hasVelocity)
            {
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    ", \"velocity_x\": {0}, \"velocity_y\": {1}, \"velocity_z\": {2}",
                    F(d.velocity.x), F(d.velocity.y), F(d.velocity.z));
            }
            sb.Append("}");
            return sb.ToString();
        }

        private static float EstimateSampleRate(List<CaptureFrame> frames)
        {
            if (frames.Count < 2) return 72f;
            double span = frames[frames.Count - 1].timeSeconds - frames[0].timeSeconds;
            return span > 0 ? (float)((frames.Count - 1) / span) : 72f;
        }

        private static string F(double v) => v.ToString("R", CultureInfo.InvariantCulture);
    }
}
