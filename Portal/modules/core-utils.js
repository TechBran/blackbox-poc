/**
 * core-utils.js
 * Foundation utilities used across all modules
 *
 * Source: Portal/app.js lines 3146-3175, 3706-3714
 * Refactored: 2025-12-20
 */

// =============================================================================
// DOM Helpers
// =============================================================================

/**
 * DOM element selector helper - shorthand for getElementById
 * @param {string} id - Element ID to find
 * @returns {HTMLElement|null} The found element or null
 */
export const $ = (id) => document.getElementById(id);

// =============================================================================
// Toast Notifications
// =============================================================================

/**
 * Display a toast notification message
 * @param {string} msg - Message to display
 */
export function toast(msg) {
    const t = $("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.remove("hide");
    setTimeout(() => t.classList.add("hide"), 2000);
}

/**
 * Display a success toast notification (green styling)
 * @param {string} msg - Success message to display
 */
export function toastSuccess(msg) {
    const t = $("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.remove("hide", "error");
    t.classList.add("success");
    setTimeout(() => {
        t.classList.add("hide");
        t.classList.remove("success");
    }, 3000);
}

/**
 * Display an error toast notification (red styling)
 * @param {string} msg - Error message to display
 */
export function toastError(msg) {
    const t = $("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.remove("hide", "success");
    t.classList.add("error");
    setTimeout(() => {
        t.classList.add("hide");
        t.classList.remove("error");
    }, 4000);
}

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Generate a simple hash for string caching purposes
 * @param {string} str - String to hash
 * @returns {string} Base36 hash string
 */
export function simpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        const char = str.charCodeAt(i);
        hash = ((hash << 5) - hash) + char;
        hash = hash & hash; // Convert to 32bit integer
    }
    return hash.toString(36);
}

// =============================================================================
// Async Utilities
// =============================================================================

/**
 * Sleep/delay helper for async operations
 * @param {number} ms - Milliseconds to wait
 * @returns {Promise<void>} Promise that resolves after delay
 */
export const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

// =============================================================================
// Debug Utilities
// =============================================================================

/**
 * Log with module prefix for debugging
 * @param {string} module - Module name
 * @param  {...any} args - Arguments to log
 */
export function debugLog(module, ...args) {
    console.log(`[${module}]`, ...args);
}
