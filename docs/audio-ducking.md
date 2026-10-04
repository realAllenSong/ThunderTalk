# Speaker ducking: investigation and restoration policy

`mute_speakers` remains enabled by default. Dictation and Studio reference
recording use the same session controller. It silences the selected output and
any active aggregate members; unrelated outputs are read-only. Imported files
and speech playback do not acquire recording sessions.

## Findings

| Suspected cause | Evidence and conclusion |
| --- | --- |
| Rounded percentages overriding native scalars | Reproduced on this Mac: both speaker channels started at `0.3125`, while AppleScript reported `31`. The old mute/restore returned both channels as `0.3100000023841858`. Its final AppleScript percentage write overwrote the preceding scalar restore. |
| Five bars becoming eight bars | The larger jump was not reproduced. Percentage rounding alone explains the measured small drift, not a jump to `0.5`. The old code also reapplied a stale target after paste and mixed snapshots from potentially different default devices. Those paths can overwrite a later user/device setting, but their occurrence in the reported incident is unconfirmed. |
| Missing master/channel controls | Real speaker output has channel `volm:1` and `volm:2`, without `volm:0`. The old implementation only used `vmvc` and master mute. The native speaker mute worked in the real reproduction; failures on AirPods/Bluetooth/HDMI were not reproduced on hardware. Fakes cover channels, missing/read-only controls, failed writes, and aggregate members. |
| False mute success | Confirmed in source: the old mute helper returned success when the property was absent. The caller considered snapshots evidence of success even if writes failed. It also applied volume zero even when mute alone succeeded. |
| Fast repeated dictations | Confirmed in source: every mute cleared and replaced the saved snapshot, even when already ducked. The single worker already serialized OS writes; the actual hazards were repeated snapshotting and delayed UI restore requests. Stop set `_recording=False` before its 250 ms microphone tail finished, permitting another start to reuse the stream. |
| Restore/post-paste race | The old worker's FIFO prevented simultaneous OS mutations. However the delayed watchdog could reset a user's later volume to the previous percentage target, and also used `percentage / 100` as a native scalar. There was no evidence that app activation itself caused a remute. The watchdog has been removed. |
| Changing output during capture | The old code snapshotted devices only at start and always used AppleScript on whichever output was current at restore. The default changed between investigation probes; a change *during recording* was tested with fakes, not induced on this user's machine. |
| User changes volume | The old implementation restored unconditionally. Fakes now test a change during capture, inside the mute write, after a crash, and with volume-zero fallback. |
| Errors/quit/crash | Stop errors bypassed the old delayed restore, settings changes could disable restoration, and quit merely queued work on a daemon without waiting. There was no recovery journal. All are corrected. |
| Studio recording | Previously did not mute at all. It now respects the setting and releases its session on finish, cancellation, startup/stop failure, and dialog close. |

The first investigation experiment had an unsafe outcome: the user reported
speakers left muted at zero during the interrupted task and restored them by
hand to output volume 50/unmuted. Its log's immediate guard readback is **not**
counted as successful final cleanup. The later all-output check below is the
restoration validation. Teams/WeMeet were not reset to guessed values.

## State and ownership

One FIFO worker owns every OS call, with a controller lock and monotonically
allocated session generations. Each recording releases its own token;
repeated/stale releases cannot restore another recording. Overlapping
recordings share the first original snapshot until the final owner releases.
The microphone starts after the mute attempt completes. A hotkey during pending
startup cancels that session; toggles during the microphone tail are ignored.
Restoration uses the captured token even if the setting changes mid-recording.

Snapshots contain native master/channel Float32 scalars and mute flags,
plus the default output's AppleScript volume/mute fallback. `volm` controls take
precedence over `vmvc`; no percent/bar/dB conversion is used for native restore.
Mute is tried first, then verified volume zero, then AppleScript mute-only.
The default route is checked immediately after microphone startup and every
100 ms while recording. Newly selected devices are snapshotted before mutation.
An aggregate's active subdevices are handled individually.

AppleScript has no device argument. Before a fallback global write, alternate
outputs are snapshotted read-only and their possible native mute changes are
journaled, so an in-call route switch/crash remains recoverable. If an alternate
output's prior mute flag cannot be read, this unsafe global fallback is skipped
with a diagnostic. Only native controls or the original device's fallback are
restored; a different output never receives an old percentage target.

Snapshots/intent are atomically persisted and fsynced **before** mutations in
`~/.thundertalk/audio-ducking.json` (mode 0600). Stable CoreAudio device UIDs,
not transient numeric IDs, identify outputs across relaunches. An advisory file
lock prevents a second instance from recovering a live recording. Failed or
disconnected restores remain journaled and retry when the device returns. For
an AppleScript-only output that is no longer current, restoration is deferred
until that UID is current again. Startup recovers regardless of the setting;
quit waits for restoration, and `atexit` is idempotent. A disk-write failure
during cleanup does not prevent restoration; the previous durable journal is
retained. One failing driver does not prevent restoring other outputs.

Detectable user changes win. If any observed volume/mute differs from our
applied state, preserve the user's volume and mute adjustment, releasing only a
still-unchanged mute applied by the app. A user unmute is not reasserted during
capture. This policy is sticky for that session and documented in Settings in
English and Chinese.

## Verification and limits

The fake backend covers exact `0.3125` restoration, unequal stereo channels,
master/channel mirrors, overlapping/rapid/duplicate sessions, cancellation and
recorder errors, changing devices and aggregates, missing/read-only controls,
failed writes/readbacks, user changes, disk failure, interrupted duck/restore,
crash recovery, a crash inside a global mute after route change, and a second
instance. Studio tests use real Qt widgets with fake recorder/audio operations.

The final hardware check took **4.811 seconds** for ten cycles. Before every
cycle it read **every output**, including native scalars/mutes and virtual
volume, then restored in `finally` and independently asserted all outputs
matched afterward. Both speaker channels were `0.5 → 0.5` in every cycle;
`mute:false → true → false`; AppleScript was `50/unmuted` before and after.
No volume writes, playback, model inference, route changes, or writes to other
outputs were allowed. Teams/WeMeet retained their observed `0/muted` states.
The final all-output snapshot matched the initial one.

See [the complete hardware log](verification/audio-ducking-hardware.txt) and
[the earlier drift reproduction](verification/audio-ducking-old.txt).
`tools/check_system_audio.py` defaults to read-only; `--run` explicitly enables
the guarded ten-cycle check.

Hardware coverage is limited to this Mac's native speaker mute. Actual
Bluetooth/AirPods profile switching and HDMI/aggregate playback were not tested.
Devices with no controllable native/AppleScript output are reported and skipped.
Route polling can allow about 100 ms of audio after a switch (plus OS latency).
Changes that return to the same value between observations, or an intentional
mute identical to the app's mute, are indistinguishable. An output newly attached
inside a global AppleScript call has no pre-attachment state to snapshot. A
killed process cannot restore until next launch; an unavailable/wedged driver
must recover before restoration can finish, so its journal is kept. Shutdown
waits are bounded. These limits do not justify guessing a user's volume.

Apple's [native volume property documentation](https://developer.apple.com/documentation/coreaudio/kaudiodevicepropertyvolumescalar)
and the installed SDK's `AudioHardware.h` describe Float32 hardware quantization;
[AudioObjectIsPropertySettable](https://developer.apple.com/documentation/coreaudio/audioobjectispropertysettable(_:_:_:))
is used before writes. Native readback verifies restoration instead of assuming
that a successful setter preserved a scalar or channel balance.
