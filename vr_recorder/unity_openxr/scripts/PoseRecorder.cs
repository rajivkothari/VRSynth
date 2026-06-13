// PoseRecorder.cs — per-frame HMD + controller pose capture.
//
// SCAFFOLDING: structure only; NOT run on hardware. Drop into a Unity OpenXR
// project (see ../README.md). The InputDevices reads are the intended approach
// but are untested here.
//
// Each Update(), reads the headset and both controllers (position, rotation,
// velocity) and buffers one frame stamped with song time. On stop, hands the
// buffer to MotionRecordingWriter.

using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR;

namespace VRSynth.Recorder
{
    public struct DeviceFrame
    {
        public Vector3 position;
        public Quaternion rotation;
        public Vector3 velocity;
        public bool hasVelocity;
        public bool tracked; // NOTE: not yet represented in MotionRecording JSON
    }

    public struct CaptureFrame
    {
        public double timeSeconds;
        public DeviceFrame head;
        public DeviceFrame left;
        public DeviceFrame right;
    }

    public class PoseRecorder : MonoBehaviour
    {
        public SongClock clock;
        public string songPath = "song.mp3";
        public float bpm = 0f;      // 0 => unknown/null on export
        public string outputPath = "capture.json";

        private readonly List<CaptureFrame> _frames = new List<CaptureFrame>();
        private bool _recording;

        public void BeginRecording()
        {
            _frames.Clear();
            if (clock != null) clock.StartSong();
            _recording = true;
        }

        public void EndRecording()
        {
            _recording = false;
            // TODO: do disk I/O off the main thread for long takes.
            MotionRecordingWriter.Write(outputPath, songPath, bpm, _frames);
        }

        private void Update()
        {
            if (!_recording) return;
            double t = clock != null ? clock.SongTimeSeconds() : 0.0;
            _frames.Add(new CaptureFrame
            {
                timeSeconds = t,
                head = Read(XRNode.Head),
                left = Read(XRNode.LeftHand),
                right = Read(XRNode.RightHand),
            });
        }

        private static DeviceFrame Read(XRNode node)
        {
            InputDevice device = InputDevices.GetDeviceAtXRNode(node);
            DeviceFrame f = new DeviceFrame { rotation = Quaternion.identity, tracked = device.isValid };

            device.TryGetFeatureValue(CommonUsages.devicePosition, out f.position);
            device.TryGetFeatureValue(CommonUsages.deviceRotation, out f.rotation);
            f.hasVelocity = device.TryGetFeatureValue(CommonUsages.deviceVelocity, out f.velocity);
            return f;
        }
    }
}
