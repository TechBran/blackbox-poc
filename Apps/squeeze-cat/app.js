/**
 * Pelvic Vibe v2.0 - Professional Dark Edition
 * Timer-centric pelvic wellness app
 */

'use strict';

// ============================================
// State Management
// ============================================

const state = {
    // Timer state
    isRunning: false,
    isPaused: false,
    phase: 'ready', // ready, squeeze, relax, complete
    currentRep: 0,
    currentSet: 1,
    timeRemaining: 5,
    timerId: null,

    // Settings
    squeezeDuration: 5,
    relaxDuration: 5,
    reps: 10,
    sets: 1,
    hapticEnabled: false,
    soundEnabled: true,

    // Progress
    weeklyProgress: [],
    streak: 0,

    // Trackers
    waterCount: 0,
    stepCount: 0,

    // Period tracker
    periods: [],
    cycleLength: 28,
    periodLength: 5
};

// ============================================
// DOM Elements (initialized after DOM ready)
// ============================================

const $ = id => document.getElementById(id);

let elements = {};

// Audio cache for countdown
let startCountdownAudio = null;
const countdownAudioCache = {};

// ============================================
// Constants
// ============================================

const CIRCUMFERENCE = 2 * Math.PI * 90; // Timer circle circumference

// ============================================
// Initialization
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    // Initialize DOM element references
    elements = {
        // Timer
        phaseLabel: $('phaseLabel'),
        timerCount: $('timerCount'),
        timerProgress: $('timerProgress'),
        breathingRing: $('breathingRing'),
        currentSet: $('currentSet'),
        totalSets: $('totalSets'),
        currentRep: $('currentRep'),
        totalReps: $('totalReps'),
        workoutDuration: $('workoutDuration'),

        // Controls
        startBtn: $('startBtn'),
        resetBtn: $('resetBtn'),
        skipBtn: $('skipBtn'),
        playIcon: $('playIcon'),
        pauseIcon: $('pauseIcon'),

        // Menu
        menuBtn: $('menuBtn'),
        menuOverlay: $('menuOverlay'),
        sideMenu: $('sideMenu'),

        // Settings
        settingsBtn: $('settingsBtn'),
        settingsModal: $('settingsModal'),
        settingsBackdrop: $('settingsBackdrop'),
        closeSettings: $('closeSettings'),
        squeezeValue: $('squeezeValue'),
        relaxValue: $('relaxValue'),
        repsValue: $('repsValue'),
        setsValue: $('setsValue'),
        hapticToggle: $('hapticToggle'),
        soundToggle: $('soundToggle'),

        // Toast
        completionToast: $('completionToast'),

        // Audio
        squeezeTone: $('squeezeTone'),
        relaxTone: $('relaxTone'),
        completeTone: $('completeTone')
    };

    // Verify critical elements exist
    if (!elements.startBtn) {
        console.error('CRITICAL: Start button not found!');
        return;
    }

    loadState();
    initUI();
    bindEvents();
    updateWeeklyBar();
    console.log('Pelvic Vibe v2.0 initialized');
});

function initUI() {
    // Set initial timer display
    elements.timerCount.textContent = state.squeezeDuration;
    state.timeRemaining = state.squeezeDuration;

    // Update settings display
    elements.squeezeValue.textContent = state.squeezeDuration + 's';
    elements.relaxValue.textContent = state.relaxDuration + 's';
    elements.repsValue.textContent = state.reps;
    elements.setsValue.textContent = state.sets;

    // Update stats display
    elements.totalSets.textContent = state.sets;
    elements.totalReps.textContent = state.reps;
    elements.currentSet.textContent = state.currentSet;
    elements.currentRep.textContent = state.currentRep;

    // Update quick info
    updateQuickInfo();

    // Set toggle states
    if (state.hapticEnabled) elements.hapticToggle.classList.add('active');
    if (state.soundEnabled) elements.soundToggle.classList.add('active');
}

// ============================================
// Event Bindings
// ============================================

function bindEvents() {
    // Timer controls
    elements.startBtn.addEventListener('click', toggleTimer);
    elements.resetBtn.addEventListener('click', resetTimer);
    elements.skipBtn.addEventListener('click', skipPhase);

    // Menu
    elements.menuBtn.addEventListener('click', openMenu);
    elements.menuOverlay.addEventListener('click', closeMenu);

    // Settings
    elements.settingsBtn.addEventListener('click', () => openModal('settingsModal'));
    elements.settingsBackdrop.addEventListener('click', () => closeModal('settingsModal'));
    elements.closeSettings.addEventListener('click', () => closeModal('settingsModal'));

    // Settings adjusters
    document.querySelectorAll('.adjust-btn').forEach(btn => {
        btn.addEventListener('click', handleSettingAdjust);
    });

    // Toggles
    elements.hapticToggle.addEventListener('click', () => {
        state.hapticEnabled = !state.hapticEnabled;
        elements.hapticToggle.classList.toggle('active');
        saveState();
    });

    elements.soundToggle.addEventListener('click', () => {
        state.soundEnabled = !state.soundEnabled;
        elements.soundToggle.classList.toggle('active');
        saveState();
    });

    // Menu items
    $('menuHowTo').addEventListener('click', () => { closeMenu(); openModal('howToModal'); });
    $('menuPoses').addEventListener('click', () => { closeMenu(); openModal('posesModal'); });
    $('menuPeriod').addEventListener('click', () => { closeMenu(); openModal('periodModal'); });
    $('menuTrackers').addEventListener('click', () => { closeMenu(); openModal('trackersModal'); updateTrackerDisplay(); });
    $('menuProgress').addEventListener('click', () => { closeMenu(); openModal('progressModal'); updateProgressModal(); });

    // Modal close buttons
    $('howToBackdrop').addEventListener('click', () => closeModal('howToModal'));
    $('closeHowTo').addEventListener('click', () => closeModal('howToModal'));
    $('posesBackdrop').addEventListener('click', () => closeModal('posesModal'));
    $('closePoses').addEventListener('click', () => closeModal('posesModal'));
    $('periodBackdrop').addEventListener('click', () => closeModal('periodModal'));
    $('closePeriod').addEventListener('click', () => closeModal('periodModal'));
    $('trackersBackdrop').addEventListener('click', () => closeModal('trackersModal'));
    $('closeTrackers').addEventListener('click', () => closeModal('trackersModal'));
    $('progressBackdrop').addEventListener('click', () => closeModal('progressModal'));
    $('closeProgress').addEventListener('click', () => closeModal('progressModal'));

    // Period tracker
    $('logPeriodStart').addEventListener('click', logPeriodStart);
    $('logPeriodEnd').addEventListener('click', logPeriodEnd);

    // Wellness trackers
    $('addWater').addEventListener('click', addWater);
    $('addSteps').addEventListener('click', addSteps);
}

// ============================================
// Timer Functions
// ============================================

function toggleTimer() {
    if (state.isRunning) {
        pauseTimer();
    } else {
        startTimer();
    }
}

function startTimer() {
    if (state.phase === 'complete') {
        resetTimer();
    }

    // First start - play countdown first
    if (state.phase === 'ready') {
        state.isRunning = true;
        state.isPaused = false;
        state.phase = 'countdown';

        elements.playIcon.classList.add('hidden');
        elements.pauseIcon.classList.remove('hidden');
        elements.phaseLabel.textContent = 'Get Ready';
        elements.phaseLabel.className = 'phase-label countdown';

        // Play countdown audio then start exercise
        playStartCountdown(() => {
            state.phase = 'squeeze';
            state.timeRemaining = state.squeezeDuration;
            state.currentRep = 1;
            playSound('squeeze');
            triggerHaptic();
            updateTimerUI();
            state.timerId = setInterval(tick, 1000);
        });
        return;
    }

    // Resume from pause
    state.isRunning = true;
    state.isPaused = false;

    updateTimerUI();
    elements.playIcon.classList.add('hidden');
    elements.pauseIcon.classList.remove('hidden');

    state.timerId = setInterval(tick, 1000);
}

function pauseTimer() {
    state.isRunning = false;
    state.isPaused = true;
    clearInterval(state.timerId);

    // Stop countdown audio if playing
    if (startCountdownAudio) {
        startCountdownAudio.pause();
        startCountdownAudio.onended = null;
    }

    // If paused during countdown, go back to ready
    if (state.phase === 'countdown') {
        state.phase = 'ready';
        state.isPaused = false;
        elements.phaseLabel.textContent = 'Ready';
        elements.phaseLabel.className = 'phase-label ready';
    }

    elements.playIcon.classList.remove('hidden');
    elements.pauseIcon.classList.add('hidden');
}

function resetTimer() {
    clearInterval(state.timerId);

    // Stop countdown audio if playing
    if (startCountdownAudio) {
        startCountdownAudio.pause();
        startCountdownAudio.onended = null;
    }

    state.isRunning = false;
    state.isPaused = false;
    state.phase = 'ready';
    state.currentRep = 0;
    state.currentSet = 1;
    state.timeRemaining = state.squeezeDuration;

    elements.playIcon.classList.remove('hidden');
    elements.pauseIcon.classList.add('hidden');
    elements.breathingRing.classList.remove('active', 'squeeze', 'relax');
    elements.timerProgress.setAttribute('class', 'timer-progress');

    updateTimerUI();
}

function skipPhase() {
    if (!state.isRunning) return;

    if (state.phase === 'squeeze') {
        switchToRelax();
    } else if (state.phase === 'relax') {
        completeRep();
    }
}

function tick() {
    state.timeRemaining--;

    if (state.timeRemaining <= 0) {
        if (state.phase === 'squeeze') {
            switchToRelax();
        } else if (state.phase === 'relax') {
            completeRep();
        }
    } else {
        // Play countdown number
        playCountdown(state.timeRemaining);

        // Play "keep holding" at midpoint of squeeze (for durations > 5s)
        if (state.phase === 'squeeze' && state.squeezeDuration > 5) {
            const midpoint = Math.floor(state.squeezeDuration / 2);
            if (state.timeRemaining === midpoint) {
                playKeepHolding();
            }
        }
    }

    updateTimerUI();
}

function switchToRelax() {
    state.phase = 'relax';
    state.timeRemaining = state.relaxDuration;
    playSound('relax');
    triggerHaptic();
    updateTimerUI();
}

function completeRep() {
    if (state.currentRep >= state.reps) {
        // Check if more sets
        if (state.currentSet < state.sets) {
            // Play set completion sound before moving to next set
            playSetComplete(state.currentSet);
            state.currentSet++;
            state.currentRep = 1;
            state.phase = 'squeeze';
            state.timeRemaining = state.squeezeDuration;
            // Delay squeeze sound slightly so set complete plays first
            setTimeout(() => playSound('squeeze'), 1500);
        } else {
            completeWorkout();
            return;
        }
    } else {
        state.currentRep++;
        state.phase = 'squeeze';
        state.timeRemaining = state.squeezeDuration;
        playSound('squeeze');
        triggerHaptic();
    }

    updateTimerUI();
}

function completeWorkout() {
    clearInterval(state.timerId);
    state.isRunning = false;
    state.phase = 'complete';

    elements.playIcon.classList.remove('hidden');
    elements.pauseIcon.classList.add('hidden');
    elements.breathingRing.classList.remove('active', 'squeeze', 'relax');

    // Mark day complete
    markDayComplete();

    // Play completion sound
    playSound('complete');
    triggerHaptic('success');

    // Show toast
    showToast();

    updateTimerUI();
}

function updateTimerUI() {
    // Update timer count
    elements.timerCount.textContent = state.timeRemaining;

    // Update phase label
    elements.phaseLabel.textContent = state.phase.charAt(0).toUpperCase() + state.phase.slice(1);
    elements.phaseLabel.className = 'phase-label ' + state.phase;

    // Update stats
    elements.currentSet.textContent = state.currentSet;
    elements.currentRep.textContent = state.currentRep;

    // Update progress ring
    let progress = 0;
    let duration = state.phase === 'squeeze' ? state.squeezeDuration : state.relaxDuration;
    if (state.phase !== 'ready' && state.phase !== 'complete') {
        progress = ((duration - state.timeRemaining) / duration) * CIRCUMFERENCE;
    }
    elements.timerProgress.style.strokeDashoffset = CIRCUMFERENCE - progress;
    elements.timerProgress.setAttribute('class', 'timer-progress ' + state.phase);

    // Update breathing ring
    if (state.isRunning && state.phase !== 'complete') {
        elements.breathingRing.classList.add('active');
        elements.breathingRing.classList.remove('squeeze', 'relax');
        elements.breathingRing.classList.add(state.phase);
    } else {
        elements.breathingRing.classList.remove('active', 'squeeze', 'relax');
    }
}

// ============================================
// Settings Functions
// ============================================

function handleSettingAdjust(e) {
    const target = e.currentTarget.dataset.target;
    const action = e.currentTarget.dataset.action;

    const limits = {
        squeeze: { min: 1, max: 30 },
        relax: { min: 1, max: 30 },
        reps: { min: 1, max: 50 },
        sets: { min: 1, max: 10 }
    };

    const stateMap = {
        squeeze: 'squeezeDuration',
        relax: 'relaxDuration',
        reps: 'reps',
        sets: 'sets'
    };

    const displayMap = {
        squeeze: 'squeezeValue',
        relax: 'relaxValue',
        reps: 'repsValue',
        sets: 'setsValue'
    };

    const step = 1;
    let value = state[stateMap[target]];

    if (action === 'increase') {
        value = Math.min(value + step, limits[target].max);
    } else {
        value = Math.max(value - step, limits[target].min);
    }

    state[stateMap[target]] = value;

    // Update display
    const suffix = (target === 'squeeze' || target === 'relax') ? 's' : '';
    elements[displayMap[target]].textContent = value + suffix;

    // Update timer display and stats
    if (target === 'squeeze' && !state.isRunning) {
        state.timeRemaining = value;
        elements.timerCount.textContent = value;
    }
    if (target === 'reps') elements.totalReps.textContent = value;
    if (target === 'sets') elements.totalSets.textContent = value;

    updateQuickInfo();
    saveState();
}

function updateQuickInfo() {
    elements.workoutDuration.textContent = `${state.squeezeDuration}s squeeze / ${state.relaxDuration}s relax`;
}

// ============================================
// Menu Functions
// ============================================

function openMenu() {
    elements.menuOverlay.classList.add('open');
    elements.sideMenu.classList.add('open');
}

function closeMenu() {
    elements.menuOverlay.classList.remove('open');
    elements.sideMenu.classList.remove('open');
}

// ============================================
// Modal Functions
// ============================================

function openModal(id) {
    $(id).classList.add('open');
}

function closeModal(id) {
    $(id).classList.remove('open');
}

// ============================================
// Weekly Progress
// ============================================

function getWeekStart() {
    const now = new Date();
    const day = now.getDay();
    const diff = now.getDate() - day;
    return new Date(now.setDate(diff)).toISOString().split('T')[0];
}

function markDayComplete() {
    const today = new Date().toISOString().split('T')[0];
    const weekStart = getWeekStart();

    // Reset if new week
    if (!state.weeklyProgress.length || state.weeklyProgress[0] < weekStart) {
        state.weeklyProgress = [];
    }

    if (!state.weeklyProgress.includes(today)) {
        state.weeklyProgress.push(today);
        state.streak++;
    }

    updateWeeklyBar();
    saveState();
}

function updateWeeklyBar() {
    const today = new Date();
    const dayOfWeek = today.getDay();

    for (let i = 0; i < 7; i++) {
        const dot = $('day' + i);
        if (!dot) continue;

        dot.classList.remove('completed', 'today');

        // Check if this day is today
        if (i === dayOfWeek) {
            dot.classList.add('today');
        }

        // Check if this day is completed
        const dayDate = new Date(today);
        dayDate.setDate(today.getDate() - dayOfWeek + i);
        const dateStr = dayDate.toISOString().split('T')[0];

        if (state.weeklyProgress.includes(dateStr)) {
            dot.classList.add('completed');
        }
    }
}

function updateProgressModal() {
    const today = new Date();
    const dayOfWeek = today.getDay();
    let completed = 0;

    for (let i = 0; i < 7; i++) {
        const check = $('progressDay' + i);
        if (!check) continue;

        check.classList.remove('completed', 'today');

        if (i === dayOfWeek) {
            check.classList.add('today');
        }

        const dayDate = new Date(today);
        dayDate.setDate(today.getDate() - dayOfWeek + i);
        const dateStr = dayDate.toISOString().split('T')[0];

        if (state.weeklyProgress.includes(dateStr)) {
            check.classList.add('completed');
            completed++;
        }
    }

    $('completedDays').textContent = completed;
    $('currentStreak').textContent = state.streak;
}

// ============================================
// Period Tracker
// ============================================

function logPeriodStart() {
    const today = new Date().toISOString().split('T')[0];
    state.periods.push({ startDate: today, endDate: null });
    updatePeriodUI();
    saveState();
}

function logPeriodEnd() {
    const today = new Date().toISOString().split('T')[0];
    const currentPeriod = state.periods.find(p => !p.endDate);
    if (currentPeriod) {
        currentPeriod.endDate = today;
    }
    updatePeriodUI();
    saveState();
}

function updatePeriodUI() {
    // Calculate averages from logged periods
    if (state.periods.length > 1) {
        // Calculate average cycle length
        let totalCycleLength = 0;
        for (let i = 1; i < state.periods.length; i++) {
            const prev = new Date(state.periods[i-1].startDate);
            const curr = new Date(state.periods[i].startDate);
            totalCycleLength += Math.round((curr - prev) / (1000 * 60 * 60 * 24));
        }
        state.cycleLength = Math.round(totalCycleLength / (state.periods.length - 1));
    }

    $('avgCycle').textContent = state.cycleLength + ' days';
    $('avgPeriod').textContent = state.periodLength + ' days';

    // Predict next period
    if (state.periods.length > 0) {
        const lastPeriod = state.periods[state.periods.length - 1];
        const lastStart = new Date(lastPeriod.startDate);
        const nextPeriod = new Date(lastStart);
        nextPeriod.setDate(nextPeriod.getDate() + state.cycleLength);
        $('nextPeriod').textContent = nextPeriod.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }
}

// ============================================
// Wellness Trackers
// ============================================

function addWater() {
    const amount = parseInt(prompt('Enter fl oz of water:', '8')) || 0;
    if (amount > 0) {
        state.waterCount += amount;
        updateTrackerDisplay();
        saveState();
    }
}

function addSteps() {
    const amount = parseInt(prompt('Enter steps:', '1000')) || 0;
    if (amount > 0) {
        state.stepCount += amount;
        updateTrackerDisplay();
        saveState();
    }
}

function updateTrackerDisplay() {
    $('waterCount').textContent = state.waterCount;
    // Calculate fill width - 100% at 64 fl oz (8 glasses x 8 oz)
    const waterGoal = 64;
    $('waterFill').style.width = Math.min((state.waterCount / waterGoal * 100), 100) + '%';

    $('stepCount').textContent = state.stepCount.toLocaleString();
    // Calculate fill width - 100% at 10,000 steps
    const stepGoal = 10000;
    $('stepFill').style.width = Math.min((state.stepCount / stepGoal * 100), 100) + '%';
}

// ============================================
// Sound & Haptics
// ============================================

function playSound(type) {
    if (!state.soundEnabled) return;

    const audio = {
        squeeze: elements.squeezeTone,
        relax: elements.relaxTone,
        complete: elements.completeTone
    }[type];

    if (audio) {
        audio.currentTime = 0;
        audio.play().catch(() => {});
    }
}

function playCountdown(number) {
    if (!state.soundEnabled) return;
    if (number < 1 || number > 30) return;

    // Use cached audio or create new
    if (!countdownAudioCache[number]) {
        countdownAudioCache[number] = new Audio(`audio/${number}.mp3`);
    }

    const audio = countdownAudioCache[number];
    audio.currentTime = 0;
    audio.play().catch(() => {});
}

function playStartCountdown(callback) {
    if (!state.soundEnabled) {
        // If sound disabled, just wait a moment then start
        setTimeout(callback, 1000);
        return;
    }

    if (!startCountdownAudio) {
        startCountdownAudio = new Audio('audio/countdown.mp3');
    }

    startCountdownAudio.currentTime = 0;
    startCountdownAudio.onended = () => {
        callback();
    };
    startCountdownAudio.play().catch(() => {
        // If audio fails, start anyway after delay
        setTimeout(callback, 2000);
    });
}

// Set completion audio cache
const setCompleteAudioCache = {};

function playSetComplete(setNumber) {
    if (!state.soundEnabled) return;
    if (setNumber < 1 || setNumber > 5) return;

    // Use cached audio or create new
    if (!setCompleteAudioCache[setNumber]) {
        setCompleteAudioCache[setNumber] = new Audio(`audio/set${setNumber}-complete.mp3`);
    }

    const audio = setCompleteAudioCache[setNumber];
    audio.currentTime = 0;
    audio.play().catch(() => {});
}

// Keep holding audio
let keepHoldingAudio = null;

function playKeepHolding() {
    if (!state.soundEnabled) return;

    if (!keepHoldingAudio) {
        keepHoldingAudio = new Audio('audio/keep-holding.mp3');
    }

    keepHoldingAudio.currentTime = 0;
    keepHoldingAudio.play().catch(() => {});
}

function triggerHaptic(type = 'medium') {
    if (!state.hapticEnabled) return;

    if ('vibrate' in navigator) {
        const patterns = {
            light: [50],
            medium: [100],
            success: [50, 50, 100]
        };
        navigator.vibrate(patterns[type] || [100]);
    }
}

// ============================================
// Toast
// ============================================

function showToast() {
    elements.completionToast.classList.add('show');
    setTimeout(() => {
        elements.completionToast.classList.remove('show');
    }, 3000);
}

// ============================================
// State Persistence
// ============================================

function saveState() {
    const saveData = {
        squeezeDuration: state.squeezeDuration,
        relaxDuration: state.relaxDuration,
        reps: state.reps,
        sets: state.sets,
        hapticEnabled: state.hapticEnabled,
        soundEnabled: state.soundEnabled,
        weeklyProgress: state.weeklyProgress,
        streak: state.streak,
        waterCount: state.waterCount,
        stepCount: state.stepCount,
        periods: state.periods,
        cycleLength: state.cycleLength,
        lastSaveDate: new Date().toISOString().split('T')[0]
    };
    localStorage.setItem('pelvicVibe', JSON.stringify(saveData));
}

function loadState() {
    const saved = localStorage.getItem('pelvicVibe');
    if (saved) {
        try {
            const data = JSON.parse(saved);

            // Reset daily trackers if new day
            const today = new Date().toISOString().split('T')[0];
            if (data.lastSaveDate !== today) {
                data.waterCount = 0;
                data.stepCount = 0;
            }

            Object.assign(state, data);
        } catch (e) {
            console.error('Failed to load saved state:', e);
        }
    }
}

console.log('Pelvic Vibe app.js loaded');
