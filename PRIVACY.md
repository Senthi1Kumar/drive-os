# DriveOS privacy notice

DriveOS is designed so that the most sensitive data stays on the vehicle. This notice lists what is stored, where it
is stored, what leaves the device and to whom, how long it is kept and how to erase it.

## 1. What stays on the device

| Data | Where | Protection | Leaves the device? |
|---|---|---|---|
| Face template | Inside the Intel RealSense ID F455 camera | Encrypted and matched by the camera; DriveOS never receives it | No |
| Link between the driver profile and the camera user id (`f455_users.json`) | `~/.local/share/drive-os/` | Owner-only file (mode 0600) | No |
| Voice template (speaker embeddings) | `~/.local/share/drive-os/voice/` | Encrypted; key file is owner-only | No |
| Raw enrollment and challenge audio, camera frames | Memory only | Never written to disk | No |
| Conversation turns (transcript and reply; spoken digits and one-time codes redacted) | `sessions.db` in the data directory | Owner-only file | Sent to OpenRouter on agent turns only (see section 2) |
| Orders and the transaction ledger | `ledger/` in the data directory | Signed, hash-chained, owner-only | No |
| Payment methods | Configuration references a provider token, never a card number | Owner-only configuration | Provider token to the payment provider when you enable one |
| Logs | `~/.local/state/drive-os/logs/` | Owner-only; contain no transcripts by default | No |
| Location | Memory; route and search requests | Not stored by DriveOS | Yes, to Mapbox (section 2) |

Biometric data (voice prints and face templates) never leaves the vehicle and is never sent to any cloud service.

## 2. What leaves the device

The optional agent, Mapbox, Exa, SMS and payment integrations make no calls until configured. Demo mode still downloads
models on demand. When the token-free map is used, it contacts OpenFreeMap for map tiles for the displayed area.

| Recipient | When | What it receives | Notes |
|---|---|---|---|
| **OpenRouter** (language-model routing) | Only when `OPENROUTER_API_KEY` is set and the agent engine is enabled, on turns the on-device rules cannot handle | The transcript of the turn, recent conversation context and the screen context the agent needs; digits and one-time codes are redacted | DriveOS requests zero-data-retention endpoints only (`zdr`) and data collection `deny`. The selected model provider's terms also apply. |
| **Mapbox** | When `DRIVEOS_MAPBOX__TOKEN` is set | Map tile requests for the area shown, route requests (origin, destination), place-search queries with the search area | Mapbox's terms and privacy policy apply. The token stays in the HMI process environment; it is not sent over the local WebSocket or written to logs. |
| **OpenFreeMap** | When the token-free map is displayed | Map tile requests for the displayed area; no account key | No DriveOS profile, biometric data or transcript is sent. |
| **Exa** (web search) | Only when `EXA_API_KEY` is set and web search is enabled, when the agent uses the search tool | A query and objective scrubbed on the device (emails, phone numbers, card digits, street addresses, coordinates and the driver's name removed); "near me" becomes a city label, never GPS; an opaque salted-hash user identifier | The key travels in a request header and is never logged. Query text is not logged unless transcript logging is turned on. |
| **Twilio** (SMS) | Only when SMS one-time codes are configured | The destination phone number and the code message | |
| **Stripe or Square** | Only when a real payment or ordering provider is configured | Order and payment data required by the provider | |
| **Hugging Face Hub** | During `driveos fetch-models` and at start-up when models are not cached | Model download requests and revision checks; no personal data | Set `HF_HUB_OFFLINE=1` after the models are downloaded. |

DriveOS does not sell personal data and does not use it for advertising.

## 3. Retention

- Conversation turns are purged after 30 days and conversation summaries after 90 days by default
  (`session.turn_retention_days`, `session.summary_retention_days`).
- Voice templates and face links are kept until you delete them.
- The transaction ledger is kept for the payment-record period that applies to your deployment.
- Logs contain no transcripts unless `DRIVEOS_LOG_TRANSCRIPTS=1` is set, which is intended for debugging only.
- Audio and camera frames are not retained.

## 4. Consent

Enrollment of a voice print or a face requires the driver's explicit consent in the enrollment flow. Withdrawing consent
means deleting the enrollment (section 5). Approvals fall back to a one-time code when no enrollment exists.

## 5. Access and erasure

```bash
driveos enroll --delete             # delete the enrolled voice print
driveos enroll --face --delete      # delete the enrolled face (and the camera entry DriveOS created)
```

To erase everything DriveOS has stored, stop the application and delete its data and state directories:

```bash
rm -rf ~/.local/share/drive-os ~/.local/share/driveos ~/.local/state/drive-os
```

Your key and configuration files live in `~/.config/drive-os/` (or `~/.config/driveos/`) and are removed the same way.
The configuration `session` section controls how long conversation history is kept. A single command that reports,
exports and erases all stored data is planned and is not part of this release.

## 6. Security measures

- Personal files and directories are created owner-only; existing ones are tightened at start-up.
- The transaction ledger is signed with a device key and hash-chained; `driveos ledger verify` detects tampering.
- The payment decision is made by DriveAuth and on-device rules, not by a language model, and the amount is re-priced
  before approval.
- Voice-ID uses a random digit challenge. The AASIST-L anti-spoof score is advisory on the reference microphone and does not approve or reject a payment by itself; DriveAuth can require Face ID or a one-time code.

## 7. Contact

Questions about this notice go to Elevatics AI: info@elevatics.com.
