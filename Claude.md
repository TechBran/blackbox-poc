# AI BlackBox Flight Recorder - Claude Code Instructions

## Overview
This is a production AI appliance system deployed on mini ITX hardware (Ryzen 7600, 32GB DDR5, NVMe) for customers. The system uses snapshot-based immutable logging with cryptographic verification.

## Critical Deployment Context
- **Network**: Private Tailscale mesh only (NOT public-facing)
- **Security**: API keys in .env acceptable for private deployment
- **Hardware**: Commercial appliance boxes sold to customers
- **Service**: Runs as systemd service `blackbox.service`
- **Access URL**: https://ai-black-box-fc-a620ai-wifi.tail401fb3.ts.net/ui/
- **Tailscale Serve**: HTTPS proxy on port 443 → localhost:9091

### Tailscale HTTPS Configuration
The system uses Tailscale's HTTPS serve feature for secure access with automatic certificates:
```bash
# Start Tailscale HTTPS proxy (runs in background)
tailscale serve --bg --https=443 http://127.0.0.1:9091

# Check Tailscale serve status
tailscale serve status

# Get certificates (if needed)
tailscale cert ai-black-box-fc-a620ai-wifi.tail401fb3.ts.net
```

**Why HTTPS is required**:
- Web Notifications API requires secure context
- Microphone/camera access requires HTTPS
- Improved performance and security
- Android WebView compatibility

## Development Snapshot Protocol

### When to Create Development Snapshots
After completing ANY of these tasks, create a development snapshot:
1. Fixing bugs or errors
2. Adding new features or UI components
3. Modifying API endpoints or backend logic
4. Updating configuration or deployment scripts
5. Performance optimizations
6. Refactoring code
7. Changing database schema or data structures
8. Updating documentation

### How to Create Development Snapshots

**IMPORTANT**: With auto-mint enabled (turns_threshold=1), you only need ONE step:

#### Send Development Summary Through Chat Endpoint
Send a detailed development log through the `/chat` endpoint with Brandon-DEV operator. Auto-mint will automatically create the snapshot capturing this conversation turn.

```bash
curl -X POST http://localhost:9091/chat \
  -H "Content-Type: application/json" \
  -d '{
    "operator": "Brandon-DEV",
    "messages": [{
      "role": "user",
      "content": "DEVELOPMENT SESSION LOG\n\n[Your detailed summary here with all changes, files, line numbers, reasoning, testing steps, etc.]"
    }]
  }'
```

**That's it!** Auto-mint (configured with turns_threshold=1) will automatically create the snapshot after the chat turn completes. The snapshot will contain your full development summary in the Raw Session Log.

**Do NOT manually mint afterward** - this will create a second empty snapshot. The auto-mint system handles snapshot creation automatically.

### Snapshot Content Requirements
Development snapshots MUST include in the chat message:
1. **Summary**: Clear 2-3 sentence overview of changes
2. **Files Modified**: List of all files changed with line numbers
3. **Changes Made**: Bullet points of specific modifications
4. **Reasoning**: Why these changes were necessary
5. **Testing**: How to test/verify the changes
6. **Deployment**: Any restart commands or migration steps needed
7. **Related Snapshots**: References to previous dev snapshots if applicable

### Snapshot Naming Convention
Use descriptive reasons in the mint call that reflect the change:
- `"Fixed timeline browser click handler"`
- `"Added mobile text wrapping CSS fixes"`
- `"Implemented Google TTS SSML validation"`
- `"Rebuilt snapshot index with operator extraction fix"`

## Architecture Notes

### Backend (Orchestrator/app.py)
- FastAPI application with threading for background tasks
- Three locks: `state_lock`, `mint_lock`, `task_lock` (held briefly)
- Background worker thread processes async tasks (TTS, image gen, etc.)
- Byte offset index for O(1) snapshot retrieval
- TF-IDF + n-gram retrieval for semantic search
- Per-operator conversation state management

### Frontend (Portal/)
- Vanilla JavaScript (no framework)
- localStorage for chat history and task persistence
- TaskManager polls every 3 seconds for async task status
- Markdown rendering with syntax highlighting
- Mobile-optimized with proper text wrapping

### Data Storage
- `Volumes/SNAPSHOT_VOLUME.txt`: Main append-only ledger
- `Manifest/snapshot_index.json`: Byte offset index
- `Archive/`: Timestamped backups with SHA-256 hashes
- `Portal/uploads/`: User-uploaded and generated media
- SQLite: Background task queue only

### Critical Config (config.ini)
```ini
[context]
recent_fossils_per_user  = 6   # Last N for continuity
keyword_fossils_per_user = 8   # Top N by TF-IDF
max_fossil_chars         = 20000
include_other_operators  = false

[auto_mint]
enable=true
turns_threshold=1
tokens_threshold=9999999

[pairing]
default_origin = https://ai-black-box-fc-a620ai-wifi.tail401fb3.ts.net/ui/
default_operator = Brandon
```

## Multimodal AI Support

### Provider Capabilities
| Provider | Text | Images | Video | Audio | PDF |
|----------|------|--------|-------|-------|-----|
| **Google Gemini** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Anthropic Claude** | ✅ | ✅ | ❌ | ❌ | ✅ |
| **OpenAI GPT** | ✅ | ✅ | ❌ | ❌ | ❌ |

### Automatic Provider Routing
The system automatically routes requests to the best provider based on media type:
- **Video/Audio detected**: Automatically switches to Google Gemini
- **PDF with OpenAI**: Automatically switches to Google Gemini
- **Images**: Supported by all providers
- **Text-only**: Uses selected provider

**Implementation** (`app.py:2561-2595`):
```python
# Auto-route based on media types
if has_video or has_audio:
    provider = "google"  # Only Gemini supports video/audio
elif has_pdf and provider == "openai":
    provider = "google"  # Gemini handles PDFs better
```

### Media Encoding by Provider
- **Google Gemini**: Uses `inline_data` format with base64 encoding
- **Anthropic Claude**: Uses base64 encoding in content blocks
- **OpenAI GPT**: Uses data URLs (`data:image/jpeg;base64,...`)

## Notification & Vibration System

### Vibration Patterns
| Event | Pattern | Duration | When |
|-------|---------|----------|------|
| **New AI Response** | Single pulse | 200ms | Any chat reply (text, image analysis, etc.) |
| **Task Complete** | Double pulse | 100ms, 50ms pause, 100ms | Image/video gen, TTS complete |
| **Task Failed** | Long pulse | 400ms | Generation failures |

### Web Notifications
Requires HTTPS (secure context). Shows notifications when:
- App is minimized or in background
- New AI response arrives: "New Message" + preview text
- Task completes: "Task Complete - Your [type] is ready!"
- Task fails: "Task Failed - Your [type] task failed"

**Implementation** (`app.js:709-802`):
```javascript
function notifyNewMessage(messagePreview) {
  vibrate([200]);  // Single pulse
  showNotification("New Message", {
    body: messagePreview,
    tag: "new-message"
  });
}
```

### Android WebView Configuration
For notifications and vibrations to work in Android app wrapper:

**AndroidManifest.xml**:
```xml
<uses-permission android:name="android.permission.VIBRATE" />
<uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
```

**MainActivity.java/WebViewActivity**:
```java
// Enable JavaScript and notifications
webSettings.setJavaScriptEnabled(true);
webSettings.setDomStorageEnabled(true);

// Auto-grant notification permissions
webView.setWebChromeClient(new WebChromeClient() {
    @Override
    public void onPermissionRequest(PermissionRequest request) {
        request.grant(request.getResources());
    }
});

// Request runtime notification permission (Android 13+)
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
    ActivityCompat.requestPermissions(this,
        new String[]{Manifest.permission.POST_NOTIFICATIONS}, 101);
}
```

## Generation Tools

### Google Cloud TTS (SSML)
**Features**:
- 300+ voices across 40+ languages
- SSML markup support for advanced control
- Pitch adjustment (-20 to +20 semitones)
- Speed adjustment (0.25x to 4.00x)
- Voice types: HD, Chirp, Wavenet, Standard

**Implementation** (`app.js:1984-2208`):
- `GoogleSSMLTool` class handles UI and API calls
- Async task processing via TaskManager
- Completion triggers double-pulse vibration + notification

**API Endpoint** (`app.py`):
```bash
POST /generate/google_ssml
{
  "voice_name": "en-US-Wavenet-A",
  "text": "Hello world",  # Or "ssml": "<speak>...</speak>"
  "speaking_rate": 1.0,
  "pitch": 0.0,
  "operator": "Brandon"
}
```

### Gemini Pro Audio (TTS)
**Features**:
- 30 preset voices (Zephyr, Puck, Charon, etc.)
- Optimized for natural conversational speech
- Fast generation via Gemini API

**Implementation** (`app.js:2214-2330`):
- `GeminiProTTSModal` class with hard-coded voice list
- Async task processing via TaskManager
- Completion triggers double-pulse vibration + notification

**API Endpoint** (`app.py`):
```bash
POST /generate/gemini_tts
{
  "text": "Hello world",
  "voice_name": "Charon",
  "operator": "Brandon"
}
```

### Image Generation (Imagen 3)
```bash
POST /generate_image
{
  "prompt": "A serene mountain landscape",
  "operator": "Brandon"
}
```

### Video Generation (Veo 3.1)
```bash
POST /generate_video
{
  "prompt": "A cat playing with a ball of yarn",
  "operator": "Brandon"
}
```

All generation tasks return `task_id` for async tracking. TaskManager polls `/task_status/{task_id}` every 3 seconds until completion.

## Common Operations

### Restart Service
Multiple methods available:

**1. Via Web UI** (Portal/app.js:2514-2530):
- Click ☰ Menu → Restart button
- Confirmation dialog appears
- Service restarts automatically
- Page reloads after 5 seconds

**2. Via API Endpoint** (`app.py:1819-1841`):
```bash
curl -X POST http://localhost:9091/restart
```

**3. Via Desktop Shortcuts** (requires passwordless sudo):
- `blackbox-restart.desktop`: Silent restart with notifications
- `blackbox-restart-terminal.desktop`: Shows terminal output

**4. Via Command Line**:
```bash
sudo systemctl restart blackbox.service
sudo journalctl -u blackbox.service -f  # Watch logs
```

**Enable Passwordless Restart** (for UI/API/shortcuts):
```bash
echo "$USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart blackbox.service" | sudo tee /etc/sudoers.d/blackbox-restart
sudo chmod 0440 /etc/sudoers.d/blackbox-restart
```

### Rebuild Snapshot Index
```bash
curl -X POST http://localhost:9091/debug/rebuild_index
```

### Test Context Retrieval
```bash
curl "http://localhost:9091/debug/context?operator=Brandon&query=test"
```

### Check Health
```bash
curl http://localhost:9091/health
```

## Development Workflow

1. **Make Changes**: Edit files as requested
2. **Test Locally**: User tests on their device
3. **Document**: Explain changes clearly to user
4. **Create Dev Snapshot**: Mint snapshot with Brandon-DEV operator
5. **Verify**: User can view in Timeline Browser (📸 button)

## Code Style
- Python: PEP 8, type hints where helpful
- JavaScript: ES6+, camelCase, clear function names
- CSS: BEM-inspired, mobile-first responsive
- No emojis unless user requests
- Professional, concise documentation

## Key Features Implemented
- ✅ Multi-provider AI (Google, Anthropic, OpenAI) with automatic routing
- ✅ **Multimodal support**: Images, videos, audio, PDFs across all providers
- ✅ **Automatic provider routing**: Switches to best provider based on media type
- ✅ **Tailscale HTTPS**: Secure access with automatic certificates
- ✅ **Notification system**: Web notifications + vibration patterns (3 types)
- ✅ **TTS generation**: Google Cloud SSML + Gemini Pro Audio
- ✅ **Image/Video generation**: Imagen 3 + Veo 3.1
- ✅ Snapshot timeline browser with search/filter
- ✅ Document upload (PDF, TXT, DOC, DOCX)
- ✅ Background task processing with TaskManager polling
- ✅ Voice input/output (STT/TTS)
- ✅ Markdown rendering with syntax highlighting
- ✅ Mobile-optimized UI with text wrapping
- ✅ Upload progress bars
- ✅ Image lightbox viewer with zoom controls
- ✅ Keyboard shortcuts
- ✅ Byte offset index for O(1) snapshot retrieval
- ✅ Auto-mint with configurable thresholds
- ✅ **Restart service**: UI button + API endpoint + desktop shortcuts
- ✅ **Android WebView support**: Notifications + vibrations on mobile

## Important Files
- `Orchestrator/app.py`: Backend FastAPI (2800+ lines)
- `Portal/app.js`: Frontend JavaScript (2800+ lines)
- `Portal/index.html`: UI structure with modals (363 lines)
- `Portal/style.css`: Mobile-first styling (1000+ lines)
- `config.ini`: System configuration
- `setup.sh`: Ubuntu deployment automation
- `CUSTOMER_SETUP.md`: User documentation
- `Claude.md`: This file - Claude Code instructions
- `blackbox-restart.desktop`: Silent restart shortcut
- `blackbox-restart-terminal.desktop`: Terminal restart shortcut

## Troubleshooting

### MagicDNS Not Resolving
**Symptoms**: `ERR_CONNECTION_REFUSED` on Android/remote devices
**Cause**: MagicDNS disabled at tailnet level or using short hostname
**Solution**:
1. Use full FQDN: `ai-black-box-fc-a620ai-wifi.tail401fb3.ts.net`
2. Enable MagicDNS in Tailscale admin console
3. Switch to HTTPS via `tailscale serve`

### HTTP Connections Blocked
**Symptoms**: "Unsafe connection" errors, microphone not working
**Cause**: Browsers require HTTPS for secure APIs (notifications, mic, camera)
**Solution**: Use Tailscale HTTPS serve (see Tailscale HTTPS Configuration section)

### Notifications Not Appearing
**Symptoms**: No notifications on Android WebView
**Cause**: Missing permissions or WebChromeClient configuration
**Solution**: Add permissions + WebChromeClient (see Android WebView Configuration)

### Config Changes Not Applied
**Symptoms**: Updated config.ini but changes not reflected
**Cause**: Service needs restart to reload configuration
**Solution**: Restart service via UI, API, desktop shortcut, or systemctl

### Image Analysis Not Working
**Symptoms**: Only Google can analyze images
**Cause**: Missing image encoding for OpenAI/Anthropic providers
**Solution**: Implemented in `app.py:2175-2288` - converts to provider-specific format

### Video/Audio Upload Fails
**Symptoms**: Provider doesn't support video/audio
**Cause**: Not all providers support all media types
**Solution**: Automatic provider routing now switches to Google Gemini (implemented `app.py:2561-2595`)

## Proactive Snapshot Creation
After completing work sessions, I will:
1. Summarize all changes made in a detailed development log
2. Send the summary through `/chat` endpoint with Brandon-DEV operator
3. Auto-mint will automatically create the snapshot (do NOT manually mint)
4. Include restart commands if needed in the summary
5. Note any configuration changes in the summary
6. Provide testing instructions in the summary

**Workflow Example**:
```bash
# Send summary with full details - auto-mint handles snapshot creation
curl -X POST http://localhost:9091/chat \
  -H "Content-Type: application/json" \
  -d '{
    "operator": "Brandon-DEV",
    "messages": [{
      "role": "user",
      "content": "DEVELOPMENT SESSION LOG\n\n[full details]"
    }]
  }'

# No manual mint needed - auto-mint (turns_threshold=1) creates snapshot automatically
```

This creates a searchable development history in the Timeline Browser with full session details captured in the Raw Session Log.
