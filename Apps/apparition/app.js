/**
 * Apparition - 3D AI Avatar Controller
 *
 * Uses TalkingHead library for 3D avatar rendering and lip-sync.
 * Integrates with BlackBox TTS for voice generation.
 */

import { TalkingHead } from 'talkinghead';

// Configuration
const CONFIG = {
    // BlackBox API endpoint
    apiBase: 'http://localhost:9091',

    // Default avatar - Male character (saved locally from Ready Player Me)
    // Original URL: https://models.readyplayer.me/6952d1bb94a2c8f39add4f4d.glb
    defaultAvatar: './models/marcus.glb',

    // Available preset avatars (stored locally in ./models/)
    // To add more: download GLB from Ready Player Me before Jan 31, 2026
    // or use alternative sources like Sketchfab, VRoid Hub, etc.
    avatars: [
        {
            id: 'marcus',
            name: 'Marcus',
            url: './models/marcus.glb',
            thumbnail: './models/marcus.png',
            body: 'M'  // M = male rig (masculine animations)
        }
    ],

    // Avatar creation URL (for help text)
    avatarCreatorUrl: 'https://demo.readyplayer.me/avatar',

    // Camera presets
    cameraViews: {
        full: { distance: 2.5, height: 0.0 },
        upper: { distance: 1.2, height: 0.5 },
        head: { distance: 0.6, height: 0.8 }
    },

    // TTS voice mapping
    voices: {
        onyx: { name: 'Onyx', description: 'Deep, authoritative' },
        nova: { name: 'Nova', description: 'Confident, commanding' },
        echo: { name: 'Echo', description: 'Warm, conversational' },
        fable: { name: 'Fable', description: 'Expressive, British' },
        shimmer: { name: 'Shimmer', description: 'Soft, ethereal' },
        alloy: { name: 'Alloy', description: 'Neutral, balanced' }
    }
};

// State
const state = {
    head: null,
    currentAvatar: CONFIG.defaultAvatar,
    currentBodyType: 'M',  // M = male (masculine stance/animations), F = female
    currentView: 'upper',
    currentMood: 'neutral',
    currentVoice: 'onyx',
    backgroundColor: '#1a1a2e',
    isSpeaking: false,
    isLoading: true,
    settings: {
        breathing: true,
        blinking: true,
        headMovement: true,
        gestures: true
    }
};

// DOM Elements
const $ = id => document.getElementById(id);
const elements = {};

// Initialize DOM references
function initElements() {
    elements.loading = $('loading');
    elements.loadingStatus = $('loadingStatus');
    elements.app = $('app');
    elements.avatar = $('avatar');
    elements.avatarStatus = $('avatarStatus');
    elements.statusText = $('statusText');
    elements.textInput = $('textInput');
    elements.speakBtn = $('speakBtn');
    elements.voiceSelect = $('voiceSelect');
    elements.settingsBtn = $('settingsBtn');
    elements.settingsPanel = $('settingsPanel');
    elements.closeSettings = $('closeSettings');
    elements.overlay = $('overlay');
    elements.fullscreenBtn = $('fullscreenBtn');
    elements.avatarGrid = $('avatarGrid');
    elements.customAvatarUrl = $('customAvatarUrl');
    elements.loadCustomAvatar = $('loadCustomAvatar');
}

// Update loading status
function updateLoadingStatus(message) {
    if (elements.loadingStatus) {
        elements.loadingStatus.textContent = message;
    }
}

// Update avatar status indicator
function setStatus(status, text) {
    elements.avatarStatus.className = 'avatar-status ' + status;
    elements.statusText.textContent = text;
}

// Show toast notification for settings feedback
function showToast(message) {
    // Remove existing toast
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    // Create toast element
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    document.body.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => {
        toast.classList.add('show');
    });

    // Remove after delay
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 2000);
}

// Initialize TalkingHead
async function initTalkingHead() {
    updateLoadingStatus('Setting up 3D renderer...');

    try {
        state.head = new TalkingHead(elements.avatar, {
            // Camera settings
            cameraDistance: CONFIG.cameraViews[state.currentView].distance,
            cameraY: CONFIG.cameraViews[state.currentView].height,

            // Lighting
            lightAmbientColor: 0x404060,
            lightAmbientIntensity: 2,
            lightDirectColor: 0xffffff,
            lightDirectIntensity: 5,
            lightSpotColor: 0xffffff,
            lightSpotIntensity: 0.5,

            // Lip sync modules
            lipsyncModules: ['en'],

            // Avatar behavior - enable by default
            avatarMood: state.currentMood,

            // Eye contact during idle vs speaking
            avatarIdleEyeContact: 0.6,
            avatarSpeakingEyeContact: 0.8,

            // Head movement during idle vs speaking
            avatarIdleHeadMove: 0.5,
            avatarSpeakingHeadMove: 0.7,

            // Stats for debugging (set to true to see FPS)
            statsNode: null,

            // Audio
            mixerGainSpeech: 1.5
        });

        updateLoadingStatus('Loading avatar model...');
        await loadAvatar(state.currentAvatar, state.currentBodyType);

        updateLoadingStatus('Avatar ready!');

        // Hide loading screen
        setTimeout(() => {
            elements.loading.classList.add('hidden');
            elements.app.style.display = 'flex';
            setStatus('', 'Ready');
        }, 500);

        state.isLoading = false;

    } catch (error) {
        console.error('Failed to initialize TalkingHead:', error);
        updateLoadingStatus('Error: ' + error.message);
    }
}

// Load avatar model
// bodyType: 'M' for male rig (masculine stance), 'F' for female rig
async function loadAvatar(url, bodyType = 'M') {
    setStatus('loading', 'Loading avatar...');

    try {
        await state.head.showAvatar({
            url: url,
            body: bodyType,
            avatarMood: state.currentMood,
            lipsyncLang: 'en'
        });

        state.currentAvatar = url;
        state.currentBodyType = bodyType;
        setStatus('', 'Ready');

        // Apply current settings
        applyIdleSettings();

    } catch (error) {
        console.error('Failed to load avatar:', error);
        setStatus('', 'Error loading avatar');
        throw error;
    }
}

// Apply idle animation settings
function applyIdleSettings() {
    if (!state.head) return;

    // Note: TalkingHead handles idle animations internally
    // These options are configured at init time, but we can
    // adjust some behaviors dynamically

    // For now, the toggles will be respected in future implementations
    // when we add custom idle animation layers
}

// Audio context for decoding
let audioContext = null;

function getAudioContext() {
    if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    return audioContext;
}

// Speak text using BlackBox TTS with audio-driven lip sync
async function speakText(text) {
    if (!text.trim() || state.isSpeaking || !state.head) return;

    state.isSpeaking = true;
    setStatus('speaking', 'Generating speech...');
    elements.speakBtn.disabled = true;

    try {
        // Call BlackBox TTS endpoint
        const response = await fetch(`${CONFIG.apiBase}/tts`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: text,
                voice: state.currentVoice,
                model: 'tts-1-hd'
            })
        });

        if (!response.ok) {
            throw new Error(`TTS request failed: ${response.status}`);
        }

        setStatus('speaking', 'Speaking...');

        // Get audio as ArrayBuffer and decode to AudioBuffer
        const arrayBuffer = await response.arrayBuffer();
        const ctx = getAudioContext();
        const audioBuffer = await ctx.decodeAudioData(arrayBuffer);

        // Create audio object for TalkingHead with the decoded buffer
        // TalkingHead's speakAudio expects: { audio: AudioBuffer, words: [], wtimes: [], wdurations: [] }
        const audioObj = {
            audio: audioBuffer,
            words: [text],  // Simple: treat entire text as one word
            wtimes: [0],    // Start at 0ms
            wdurations: [audioBuffer.duration * 1000]  // Duration in ms
        };

        // Play through TalkingHead with lip sync
        await state.head.speakAudio(audioObj, {
            lipsyncLang: 'en'
        });

    } catch (error) {
        console.error('TTS Error:', error);
        setStatus('', 'TTS Error - check console');

        // Try simple audio playback as fallback
        try {
            const response = await fetch(`${CONFIG.apiBase}/tts`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, voice: state.currentVoice, model: 'tts-1-hd' })
            });
            const blob = await response.blob();
            const audio = new Audio(URL.createObjectURL(blob));
            audio.play();

            // Simulate speaking animation while audio plays
            if (state.head && state.head.setMood) {
                state.head.setMood('happy');
            }
            await new Promise(resolve => {
                audio.onended = resolve;
            });
            if (state.head && state.head.setMood) {
                state.head.setMood(state.currentMood);
            }
        } catch (fallbackError) {
            console.error('Fallback audio also failed:', fallbackError);
        }
    } finally {
        state.isSpeaking = false;
        setStatus('', 'Ready');
        elements.speakBtn.disabled = false;
    }
}

// Change camera view
// TalkingHead supports: "full", "mid", "upper", "head"
function setCameraView(view) {
    if (!state.head) return;

    state.currentView = view;

    // Map our view names to TalkingHead view names
    const viewMap = {
        'full': 'full',
        'upper': 'upper',
        'head': 'head'
    };

    const thView = viewMap[view] || 'upper';

    try {
        // TalkingHead setView method
        state.head.setView(thView);
        console.log('Camera view set to:', thView);
    } catch (error) {
        console.error('Failed to set camera view:', error);
    }

    // Update UI
    document.querySelectorAll('.view-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === view);
    });
}

// Change mood
// TalkingHead moods: neutral, happy, angry, sad, fear, disgust, love, sleep
function setMood(mood) {
    if (!state.head) return;

    state.currentMood = mood;

    try {
        state.head.setMood(mood);
        console.log('Mood set to:', mood);
    } catch (error) {
        console.error('Failed to set mood:', error);
    }

    // Update UI
    document.querySelectorAll('.mood-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mood === mood);
    });
}

// Change background color
function setBackgroundColor(color) {
    state.backgroundColor = color;

    // Set container background (CSS fallback)
    const container = elements.avatar;
    const avatarContainer = document.getElementById('avatarContainer');

    if (color === 'transparent' || color === '#transparent') {
        container.style.background = 'transparent';
        avatarContainer.style.background = 'linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)';
    } else {
        container.style.background = color;
        avatarContainer.style.background = color;
    }

    // Try to set Three.js renderer background if accessible
    try {
        // TalkingHead may expose renderer differently
        const renderer = state.head?.renderer || state.head?.three?.renderer;
        if (renderer && renderer.setClearColor) {
            if (color === 'transparent' || color === '#transparent') {
                renderer.setClearColor(0x000000, 0);
            } else {
                const colorInt = parseInt(color.replace('#', ''), 16);
                renderer.setClearColor(colorInt, 1);
            }
        }
    } catch (error) {
        console.log('Could not set renderer background:', error);
    }

    console.log('Background set to:', color);

    // Update UI
    document.querySelectorAll('.color-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.bg === color);
    });
}

// Populate avatar grid
function populateAvatarGrid() {
    elements.avatarGrid.innerHTML = CONFIG.avatars.map(avatar => `
        <div class="avatar-thumb ${avatar.url === state.currentAvatar ? 'active' : ''}"
             data-url="${avatar.url}"
             data-body="${avatar.body || 'F'}"
             title="${avatar.name}">
            ${avatar.thumbnail
                ? `<img src="${avatar.thumbnail}" alt="${avatar.name}" onerror="this.style.display='none'">`
                : `<div class="avatar-placeholder">${avatar.name.charAt(0)}</div>`
            }
        </div>
    `).join('');

    // Add click handlers
    elements.avatarGrid.querySelectorAll('.avatar-thumb').forEach(thumb => {
        thumb.addEventListener('click', async () => {
            const url = thumb.dataset.url;
            const bodyType = thumb.dataset.body || 'F';
            if (url !== state.currentAvatar) {
                try {
                    await loadAvatar(url, bodyType);
                    document.querySelectorAll('.avatar-thumb').forEach(t => {
                        t.classList.toggle('active', t.dataset.url === url);
                    });
                } catch (error) {
                    console.error('Failed to switch avatar:', error);
                }
            }
        });
    });
}

// Settings panel
function openSettings() {
    elements.settingsPanel.classList.add('open');
    elements.overlay.classList.add('visible');
}

function closeSettings() {
    elements.settingsPanel.classList.remove('open');
    elements.overlay.classList.remove('visible');
}

// Toggle fullscreen
function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
    } else {
        document.exitFullscreen();
    }
}

// Event listeners
function setupEventListeners() {
    // Speak button
    elements.speakBtn.addEventListener('click', () => {
        speakText(elements.textInput.value);
    });

    // Enter key to speak
    elements.textInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            speakText(elements.textInput.value);
        }
    });

    // Voice selector
    elements.voiceSelect.addEventListener('change', (e) => {
        state.currentVoice = e.target.value;
    });

    // Settings panel
    elements.settingsBtn.addEventListener('click', openSettings);
    elements.closeSettings.addEventListener('click', closeSettings);
    elements.overlay.addEventListener('click', closeSettings);

    // Fullscreen
    elements.fullscreenBtn.addEventListener('click', toggleFullscreen);

    // View buttons
    document.querySelectorAll('.view-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            console.log('View button clicked:', btn.dataset.view);
            setCameraView(btn.dataset.view);
            showToast(`Camera: ${btn.textContent}`);
        });
    });

    // Mood buttons
    document.querySelectorAll('.mood-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            console.log('Mood button clicked:', btn.dataset.mood);
            setMood(btn.dataset.mood);
            showToast(`Mood: ${btn.textContent}`);
        });
    });

    // Background colors
    document.querySelectorAll('.color-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            console.log('Background button clicked:', btn.dataset.bg);
            setBackgroundColor(btn.dataset.bg);
            showToast('Background updated');
        });
    });

    // Custom avatar
    elements.loadCustomAvatar.addEventListener('click', async () => {
        const url = elements.customAvatarUrl.value.trim();
        const bodyTypeSelect = $('customBodyType');
        const bodyType = bodyTypeSelect ? bodyTypeSelect.value : 'F';
        if (url) {
            try {
                await loadAvatar(url, bodyType);
                elements.customAvatarUrl.value = '';
            } catch (error) {
                alert('Failed to load custom avatar. Make sure the URL is a valid GLB file.');
            }
        }
    });

    // Idle behavior toggles
    ['Breathing', 'Blinking', 'HeadMovement', 'Gestures'].forEach(setting => {
        const toggle = $(`toggle${setting}`);
        if (toggle) {
            toggle.addEventListener('change', (e) => {
                state.settings[setting.toLowerCase()] = e.target.checked;
                applyIdleSettings();
            });
        }
    });

    // Handle window resize
    window.addEventListener('resize', () => {
        if (state.head && state.head.renderer) {
            const container = elements.avatar;
            state.head.renderer.setSize(container.clientWidth, container.clientHeight);
        }
    });
}

// Initialize app
async function init() {
    initElements();
    populateAvatarGrid();
    setupEventListeners();
    await initTalkingHead();
}

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// Export for debugging
window.apparition = {
    state,
    speak: speakText,
    setMood,
    setCameraView,
    loadAvatar
};
