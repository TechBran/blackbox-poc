/**
 * help-hints.js
 * Interactive home screen for BlackBox Portal
 *
 * Shows an interactive landing page with feature cards when chat is empty.
 * Automatically hides when messages appear.
 * Feature cards display in a rotating carousel on ALL screen sizes.
 *
 * Refactored: 2026-01-13
 */

import { $ } from './core-utils.js';

// =============================================================================
// State
// =============================================================================

let carouselInterval = null;
let currentCardIndex = 0;
const CAROUSEL_INTERVAL_MS = 4000; // 4 seconds per card

// =============================================================================
// Initialization
// =============================================================================

/**
 * Initialize home screen system
 */
export function initHelpHints() {
    console.log('[HOME] Initializing home screen');
    const homeScreen = $("helpHints");

    if (!homeScreen) {
        console.log('[HOME] Home screen element not found');
        return;
    }

    // Add click handlers to feature cards
    const featureCards = homeScreen.querySelectorAll('.feature-card');
    featureCards.forEach(card => {
        card.addEventListener('click', () => handleFeatureClick(card));
    });

    // Create carousel dots
    createCarouselDots(homeScreen, featureCards.length);

    // Start carousel (always active on all screen sizes)
    startCarousel(homeScreen);

    // Check if history is empty on load
    updateHintsVisibility();
}

// =============================================================================
// Carousel System (all screen sizes)
// =============================================================================

/**
 * Create carousel dots indicator
 */
function createCarouselDots(homeScreen, count) {
    const featuresContainer = homeScreen.querySelector('.home-features');
    if (!featuresContainer) return;

    // Check if dots already exist
    if (homeScreen.querySelector('.carousel-dots')) return;

    const dotsContainer = document.createElement('div');
    dotsContainer.className = 'carousel-dots';

    for (let i = 0; i < count; i++) {
        const dot = document.createElement('div');
        dot.className = 'carousel-dot' + (i === 0 ? ' active' : '');
        dot.addEventListener('click', () => {
            // Reset interval when user clicks a dot
            if (carouselInterval) {
                clearInterval(carouselInterval);
                carouselInterval = setInterval(() => {
                    const cards = homeScreen.querySelectorAll('.feature-card');
                    currentCardIndex = (currentCardIndex + 1) % cards.length;
                    goToCard(homeScreen, currentCardIndex);
                }, CAROUSEL_INTERVAL_MS);
            }
            goToCard(homeScreen, i);
        });
        dotsContainer.appendChild(dot);
    }

    // Insert after features
    featuresContainer.after(dotsContainer);
}

/**
 * Start carousel rotation
 */
function startCarousel(homeScreen) {
    if (carouselInterval) return; // Already running

    const cards = homeScreen.querySelectorAll('.feature-card');
    if (cards.length === 0) return;

    // Set initial active card
    goToCard(homeScreen, currentCardIndex);

    // Start rotation
    carouselInterval = setInterval(() => {
        currentCardIndex = (currentCardIndex + 1) % cards.length;
        goToCard(homeScreen, currentCardIndex);
    }, CAROUSEL_INTERVAL_MS);

    console.log('[HOME] Carousel started with', cards.length, 'cards');
}

/**
 * Stop carousel rotation
 */
function stopCarousel() {
    if (carouselInterval) {
        clearInterval(carouselInterval);
        carouselInterval = null;
        console.log('[HOME] Carousel stopped');
    }
}

/**
 * Go to specific card
 */
function goToCard(homeScreen, index) {
    const cards = homeScreen.querySelectorAll('.feature-card');
    const dots = homeScreen.querySelectorAll('.carousel-dot');

    if (cards.length === 0) return;

    currentCardIndex = index;

    // Update cards
    cards.forEach((card, i) => {
        if (i === index) {
            card.classList.add('carousel-active');
        } else {
            card.classList.remove('carousel-active');
        }
    });

    // Update dots
    dots.forEach((dot, i) => {
        if (i === index) {
            dot.classList.add('active');
        } else {
            dot.classList.remove('active');
        }
    });
}

// =============================================================================
// Visibility Control
// =============================================================================

/**
 * Update home screen visibility based on history content
 */
export function updateHintsVisibility() {
    const homeScreen = $("helpHints");
    const history = $("history");

    if (!homeScreen || !history) {
        console.log('[HOME] Missing elements, cannot update visibility');
        return;
    }

    // Count actual bubble elements (not the home screen itself)
    const bubbles = history.querySelectorAll('.bubble');
    const isEmpty = bubbles.length === 0;

    if (isEmpty) {
        console.log('[HOME] No messages - showing home screen');
        homeScreen.classList.remove('hide');
    } else {
        console.log('[HOME] Messages present - hiding home screen');
        homeScreen.classList.add('hide');
    }
}

// =============================================================================
// Feature Card Interactions
// =============================================================================

/**
 * Handle click on a feature card
 * @param {HTMLElement} card - The clicked feature card
 */
function handleFeatureClick(card) {
    const feature = card.dataset.feature;
    console.log('[HOME] Feature card clicked:', feature);

    // Add a brief visual feedback
    card.style.transform = 'scale(0.95)';
    setTimeout(() => {
        card.style.transform = '';
    }, 150);

    // Could be extended to show detailed modals or navigate to features
    // For now, just provides visual feedback
}

// =============================================================================
// Manual Control (exported for other modules)
// =============================================================================

/**
 * Hide home screen
 */
export function hideHints() {
    const homeScreen = $("helpHints");
    if (homeScreen) {
        homeScreen.classList.add('hide');
    }
}

/**
 * Show home screen
 */
export function showHints() {
    const homeScreen = $("helpHints");
    if (homeScreen) {
        homeScreen.classList.remove('hide');
    }
}

// Legacy exports for backwards compatibility
export function startHintRotation() {}
export function stopHintRotation() {}
export function rotateToNextHint() {}
export function showHint() {}
