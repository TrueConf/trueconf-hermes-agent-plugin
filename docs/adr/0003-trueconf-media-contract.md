---
status: accepted
---

# Match version 0.1.0 media delivery to the TrueConf API

TrueConf does not currently expose native outbound video or voice-message send
operations. Version 0.1.0 will therefore preserve outbound video and audio as
generic file attachments through the public SDK instead of inventing native
semantics in the Plugin or patching the SDK. Native inbound video and voice
remain supported when TrueConf Server emits their corresponding event types.

TrueConf Server's advertised attachment size is authoritative for inbound
download limits. The Plugin and SDK will reject an attachment whose reported
size exceeds the configured limit before reading its body; they are not
required to impose a second byte-counting limit on the downloaded stream. This
trust boundary is appropriate because the authenticated TrueConf Server, not
an arbitrary remote origin, supplies both the metadata and file content.

## Consequences

- Version 0.1.0 promises delivery of outbound video/audio files, not a native
  video player or voice-message bubble.
- A future native TrueConf send API can extend the observable behavior through
  a later decision and SDK capability.
- The download limit protects against files the server reports as oversized;
  defending against a compromised or internally inconsistent TrueConf Server
  is outside the current threat model.
- Hermes' public zero value for an unlimited inbound-media cap is translated
  to `sys.maxsize` only at the SDK constructor boundary because the SDK
  requires a positive in-memory limit. Hermes preflight retains the original
  zero value.
- Native outbound image presentation uses `Bot.send_photo()` with a cloned
  `InputFile` as its preview. Live beta-server evidence showed that the SDK's
  accepted `preview=None` form is rendered as a generic file.
