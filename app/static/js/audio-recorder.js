/**
 * Audio Recorder Worklet
 */

export async function startAudioRecorderWorklet(audioRecorderHandler) {
  const audioRecorderContext = new AudioContext({ sampleRate: 16000 });
  const workletURL = new URL("./pcm-recorder-processor.js", import.meta.url);
  await audioRecorderContext.audioWorklet.addModule(workletURL);

  const micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1 },
  });
  const source = audioRecorderContext.createMediaStreamSource(micStream);

  const audioRecorderNode = new AudioWorkletNode(
    audioRecorderContext,
    "pcm-recorder-processor"
  );

  source.connect(audioRecorderNode);
  audioRecorderNode.port.onmessage = (event) => {
    const pcmData = convertFloat32ToPCM(event.data);
    audioRecorderHandler(pcmData);
  };
  return [audioRecorderNode, audioRecorderContext, micStream];
}

function convertFloat32ToPCM(inputData) {
  const pcm16 = new Int16Array(inputData.length);
  for (let i = 0; i < inputData.length; i++) {
    pcm16[i] = inputData[i] * 0x7fff;
  }
  return pcm16.buffer;
}
