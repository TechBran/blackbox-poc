# WorkoutVibe - Universal Workout Timer

A modern Android workout timer app with multiple workout types, dual-voice TTS, and workout tracking.

## Features

### Workout Types
- **Push-ups** - 2s work / 3s rest, 10 reps x 3 sets
- **Sit-ups** - 2s work / 3s rest, 15 reps x 3 sets
- **Squats** - 3s work / 3s rest, 12 reps x 3 sets
- **Planks** - 30s hold / 15s rest, 1 rep x 3 sets
- **Burpees** - 3s work / 5s rest, 8 reps x 3 sets
- **Running Intervals** - 60s run / 30s walk, 1 rep x 8 sets
- **Sprint Intervals** - 20s sprint / 40s recover, 1 rep x 10 sets
- **Custom** - Configure your own timing

### Voice Options
- Female voice (default)
- Male voice (Onyx)

### Tracking
- Weekly progress with streak tracking
- Calendar view with workout history
- Daily workout goals (1-10 workouts/day)
- Step tracking with daily goal

## Requirements

- Android Studio Hedgehog (2023.1.1) or later
- Kotlin 1.9.20+
- Android SDK 34
- Minimum SDK: Android 8.0 (API 26)

## Building the App

1. Open the project folder in Android Studio
2. Wait for Gradle sync to complete
3. Add audio files to `app/src/main/res/raw/` (see Audio Setup below)
4. Click "Run" or press Shift+F10

## Audio Setup

Audio files are stored in `res/raw/` with the naming convention:
- `f_*` - Female voice files
- `m_*` - Male voice files

### Required Audio Files

**Legacy files (used as fallback):**
```
countdown.mp3           # Initial 3-2-1-Go countdown
squeeze.mp3             # Work phase cue (legacy)
relax.mp3               # Rest phase cue (legacy)
keep_holding.mp3        # Halfway encouragement
workout_complete.mp3    # Completion announcement
set1_complete.mp3       # Set 1 complete
set2_complete.mp3       # Set 2 complete
set3_complete.mp3       # Set 3 complete
set4_complete.mp3       # Set 4 complete
set5_complete.mp3       # Set 5 complete
num_1.mp3 - num_30.mp3  # Countdown numbers
```

**New prefixed files (for full voice support):**
```
f_num_1.mp3 - f_num_60.mp3    # Female numbers 1-60
m_num_1.mp3 - m_num_60.mp3    # Male numbers 1-60

f_pushup.mp3, m_pushup.mp3    # Workout cues
f_squat.mp3, m_squat.mp3
f_hold.mp3, m_hold.mp3
f_run.mp3, m_run.mp3
f_sprint.mp3, m_sprint.mp3
f_go.mp3, m_go.mp3

f_rest.mp3, m_rest.mp3        # Rest cues
f_walk.mp3, m_walk.mp3
f_recover.mp3, m_recover.mp3

f_countdown.mp3, m_countdown.mp3
f_workout_complete.mp3, m_workout_complete.mp3
f_keep_going.mp3, m_keep_going.mp3
f_set_complete_1.mp3 - f_set_complete_10.mp3
m_set_complete_1.mp3 - m_set_complete_10.mp3
```

## Project Structure

```
app/src/main/
├── java/com/workoutvibe/app/
│   ├── MainActivity.kt           # Main entry point
│   ├── WorkoutVibeApp.kt         # Application class
│   ├── audio/
│   │   ├── AudioManager.kt       # Audio playback with voice selection
│   │   ├── HapticManager.kt      # Vibration patterns
│   │   └── TTSIndex.kt           # Audio file registry
│   ├── data/
│   │   ├── AppPreferences.kt     # DataStore persistence
│   │   ├── VoiceGender.kt        # Voice selection enum
│   │   ├── WorkoutSettings.kt    # Data models
│   │   └── WorkoutType.kt        # Workout type definitions
│   ├── service/
│   │   └── WorkoutService.kt     # Background timer service
│   ├── ui/
│   │   ├── components/
│   │   │   ├── NavigationDrawer.kt
│   │   │   ├── TimerRing.kt      # Circular progress
│   │   │   └── WeeklyDots.kt     # Weekly progress bar
│   │   ├── screens/
│   │   │   ├── TimerScreen.kt    # Main workout screen
│   │   │   ├── SettingsDialog.kt
│   │   │   ├── WorkoutSelectorScreen.kt
│   │   │   ├── CalendarScreen.kt
│   │   │   ├── StepTrackerScreen.kt
│   │   │   └── ProgressScreen.kt
│   │   └── theme/
│   │       ├── Color.kt
│   │       ├── Theme.kt
│   │       └── Type.kt
│   └── viewmodel/
│       └── MainViewModel.kt      # State management
├── res/
│   ├── drawable/                 # Vector icons
│   ├── mipmap-anydpi-v26/        # Adaptive icons
│   ├── raw/                      # Audio files
│   ├── values/
│   │   ├── colors.xml
│   │   ├── strings.xml
│   │   └── themes.xml
│   └── xml/                      # Backup rules
└── AndroidManifest.xml
```

## Architecture

- **MVVM** with Jetpack ViewModel
- **Compose** for declarative UI
- **DataStore** for preferences persistence
- **StateFlow** for reactive state management
- **Coroutines** for async operations

## Color Scheme

- **Primary**: Red (#E53935) - Work phase
- **Secondary**: Teal (#26A69A) - Rest phase
- **Background**: Dark (#0D0D0D)
- **Complete**: Green (#69F0AE)

## Permissions

- `VIBRATE` - Haptic feedback
- `FOREGROUND_SERVICE` - Background workout timer
- `POST_NOTIFICATIONS` - Workout status notifications

## Customization

### Colors
Edit `ui/theme/Color.kt` to change the color scheme.

### Settings Limits
Edit `data/WorkoutSettings.kt` to change min/max values for durations, reps, and sets.

### Workout Types
Edit `data/WorkoutType.kt` to add or modify workout types.

## Version

v3.0.0 - WorkoutVibe Universal Timer

## License

Proprietary - WorkoutVibe
