// AI BlackBox Landing Page - JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // FAQ Accordion
    const faqItems = document.querySelectorAll('.faq-item');

    faqItems.forEach(item => {
        const question = item.querySelector('.faq-question');

        question.addEventListener('click', () => {
            // Close other items
            faqItems.forEach(otherItem => {
                if (otherItem !== item && otherItem.classList.contains('active')) {
                    otherItem.classList.remove('active');
                }
            });

            // Toggle current item
            item.classList.toggle('active');
        });
    });

    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function(e) {
            e.preventDefault();
            const target = document.querySelector(this.getAttribute('href'));
            if (target) {
                const navHeight = document.querySelector('.nav').offsetHeight;
                const targetPosition = target.getBoundingClientRect().top + window.pageYOffset - navHeight;
                window.scrollTo({
                    top: targetPosition,
                    behavior: 'smooth'
                });
            }
        });
    });

    // Stripe Checkout - Live Payment Link
    // All checkout buttons now use direct Stripe Payment Link
    // https://buy.stripe.com/aFa3cufqIdLAgVk8Ib4ow00

    // Navbar scroll effect
    const nav = document.querySelector('.nav');
    let lastScroll = 0;

    window.addEventListener('scroll', () => {
        const currentScroll = window.pageYOffset;

        if (currentScroll > 100) {
            nav.style.background = 'rgba(10, 10, 11, 0.95)';
        } else {
            nav.style.background = 'rgba(10, 10, 11, 0.8)';
        }

        lastScroll = currentScroll;
    });

    // Animate elements on scroll
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.style.opacity = '1';
                entry.target.style.transform = 'translateY(0)';
            }
        });
    }, observerOptions);

    // Observe feature cards and steps
    document.querySelectorAll('.feature-card, .step, .pricing-card').forEach(el => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(20px)';
        el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
        observer.observe(el);
    });

    // Counter animation for stats
    function animateCounter(element, target, duration = 2000) {
        let start = 0;
        const increment = target / (duration / 16);

        function updateCounter() {
            start += increment;
            if (start < target) {
                element.textContent = Math.floor(start).toLocaleString();
                requestAnimationFrame(updateCounter);
            } else {
                element.textContent = target.toLocaleString();
            }
        }

        updateCounter();
    }

    // Animate stats when they come into view
    const statsObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const statValues = entry.target.querySelectorAll('.stat-value');
                statValues.forEach(stat => {
                    const text = stat.textContent;
                    if (text.includes('$')) {
                        // Already formatted
                    } else if (!isNaN(parseInt(text))) {
                        // Animate numbers
                    }
                });
                statsObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.5 });

    const heroStats = document.querySelector('.hero-stats');
    if (heroStats) {
        statsObserver.observe(heroStats);
    }

    console.log('AI BlackBox Landing Page loaded');

    // ========================================
    // MEMORY SECTION INTERACTIVITY
    // ========================================

    // Orbit Items - Click to toggle active state and show tooltip
    const orbitItems = document.querySelectorAll('.orbit-item');
    orbitItems.forEach(item => {
        item.addEventListener('click', () => {
            // Toggle active state
            const wasActive = item.classList.contains('active');

            // Remove active from all orbit items
            orbitItems.forEach(i => i.classList.remove('active'));

            // Toggle current item
            if (!wasActive) {
                item.classList.add('active');
            }
        });
    });

    // Memory Feature Cards - Click to toggle active state
    const memFeatures = document.querySelectorAll('.mem-feature');
    memFeatures.forEach(feature => {
        feature.addEventListener('click', () => {
            // Toggle active state
            const wasActive = feature.classList.contains('active');

            // Remove active from all features
            memFeatures.forEach(f => f.classList.remove('active'));

            // Toggle current feature
            if (!wasActive) {
                feature.classList.add('active');
            }
        });
    });

    // Exosuit Capabilities - Click to toggle active state
    const exoCaps = document.querySelectorAll('.exo-cap');
    exoCaps.forEach(cap => {
        cap.addEventListener('click', () => {
            // Toggle active state
            const wasActive = cap.classList.contains('active');

            // Remove active from all caps
            exoCaps.forEach(c => c.classList.remove('active'));

            // Toggle current cap
            if (!wasActive) {
                cap.classList.add('active');
            }
        });
    });

    // Click outside to deselect all
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.orbit-item') &&
            !e.target.closest('.mem-feature') &&
            !e.target.closest('.exo-cap')) {
            document.querySelectorAll('.orbit-item.active, .mem-feature.active, .exo-cap.active')
                .forEach(el => el.classList.remove('active'));
        }
    });

    // ========================================
    // CINEMATIC CANVAS PARTICLE SYSTEM
    // Multi-layered with depth, trails, and glow
    // ========================================

    // MODE: 'embers' or 'fireflies'
    const PARTICLE_MODE = 'embers';

    function initCinematicParticles(mode) {
        // Create canvas
        const canvas = document.createElement('canvas');
        canvas.id = 'particle-canvas';
        canvas.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 9999;
        `;
        document.body.insertBefore(canvas, document.body.firstChild);

        const ctx = canvas.getContext('2d');
        let width, height;
        let particles = [];
        let mouseX = 0, mouseY = 0;

        // Resize handler
        function resize() {
            width = canvas.width = window.innerWidth;
            height = canvas.height = window.innerHeight;
        }
        resize();
        window.addEventListener('resize', resize);

        // Track mouse for interactive effects
        document.addEventListener('mousemove', (e) => {
            mouseX = e.clientX;
            mouseY = e.clientY;
        });

        // Configuration
        const configs = {
            embers: {
                particleCount: 120,
                layers: [
                    { count: 40, speed: 0.3, size: [0.5, 1], opacity: 0.25, blur: 2 },    // Far background - tiny
                    { count: 50, speed: 0.5, size: [1, 2], opacity: 0.4, blur: 1 },       // Mid layer - small
                    { count: 30, speed: 0.8, size: [1.5, 3], opacity: 0.7, blur: 0 }      // Foreground - medium
                ],
                colors: [
                    { r: 255, g: 74, b: 74 },    // Red
                    { r: 255, g: 120, b: 50 },   // Orange
                    { r: 255, g: 180, b: 50 },   // Yellow-orange
                    { r: 255, g: 220, b: 100 },  // Yellow
                    { r: 255, g: 250, b: 200 }   // White-hot
                ],
                colorWeights: [0.3, 0.3, 0.2, 0.15, 0.05],
                glowIntensity: 10,
                turbulence: 0.6,
                riseSpeed: 0.8,
                flickerSpeed: 0.015,
                trailLength: 2
            },
            fireflies: {
                particleCount: 80,
                layers: [
                    { count: 25, speed: 0.2, size: [2, 3], opacity: 0.4, blur: 3 },
                    { count: 35, speed: 0.4, size: [3, 5], opacity: 0.6, blur: 1 },
                    { count: 20, speed: 0.6, size: [4, 7], opacity: 0.9, blur: 0 }
                ],
                colors: [
                    { r: 127, g: 255, b: 0 },    // Chartreuse
                    { r: 173, g: 255, b: 47 },   // Green-yellow
                    { r: 200, g: 255, b: 100 },  // Light green
                    { r: 255, g: 255, b: 150 }   // Warm white
                ],
                colorWeights: [0.35, 0.35, 0.2, 0.1],
                glowIntensity: 25,
                turbulence: 1.5,
                riseSpeed: 0.4,
                flickerSpeed: 0.03,
                blinkCycle: true,
                trailLength: 0
            }
        };

        const config = configs[mode];

        // Particle class
        class Particle {
            constructor(layer, layerIndex) {
                this.layer = layer;
                this.layerIndex = layerIndex;
                this.reset();
            }

            reset() {
                this.x = Math.random() * width;
                this.y = height + Math.random() * 100;
                this.size = this.layer.size[0] + Math.random() * (this.layer.size[1] - this.layer.size[0]);
                this.baseSize = this.size;

                // Pick weighted random color
                const rand = Math.random();
                let cumulative = 0;
                for (let i = 0; i < config.colorWeights.length; i++) {
                    cumulative += config.colorWeights[i];
                    if (rand < cumulative) {
                        this.color = { ...config.colors[i] };
                        break;
                    }
                }
                if (!this.color) this.color = { ...config.colors[0] };

                // Movement properties
                this.vx = (Math.random() - 0.5) * 2 * this.layer.speed;
                this.vy = -(0.5 + Math.random() * 0.5) * config.riseSpeed * this.layer.speed;
                this.baseVy = this.vy;

                // Oscillation for organic movement - very gentle
                this.oscillationOffset = Math.random() * Math.PI * 2;
                this.oscillationSpeed = 0.005 + Math.random() * 0.008;
                this.oscillationAmplitude = 5 + Math.random() * 10;

                // Flicker/blink properties
                this.flickerOffset = Math.random() * Math.PI * 2;
                this.flickerSpeed = config.flickerSpeed * (0.8 + Math.random() * 0.4);
                this.opacity = this.layer.opacity;
                this.baseOpacity = this.layer.opacity;

                // Firefly specific: blink cycle
                if (config.blinkCycle) {
                    this.blinkPhase = Math.random() * Math.PI * 2;
                    this.blinkSpeed = 0.02 + Math.random() * 0.02;
                    this.nextBlink = Math.random() * 200;
                    this.isBlinking = false;
                    this.blinkIntensity = 0;
                }

                // Trail positions
                this.trail = [];
                this.life = 1;
            }

            update(time) {
                // Very gentle turbulence / wind effect
                const turbX = Math.sin(time * 0.0003 + this.oscillationOffset) * config.turbulence * 0.3;
                const turbY = Math.cos(time * 0.0004 + this.oscillationOffset) * config.turbulence * 0.15;

                // Very gentle horizontal oscillation (smooth swaying)
                const oscillation = Math.sin(time * this.oscillationSpeed + this.oscillationOffset) * this.oscillationAmplitude * 0.002;

                // Mouse interaction (very subtle)
                const dx = mouseX - this.x;
                const dy = mouseY - this.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                let mouseInfluence = 0;
                if (dist < 150) {
                    mouseInfluence = (150 - dist) / 150 * 0.1;
                }

                // Update position with smooth interpolation
                this.vx += (turbX * 0.005 + oscillation - this.vx * 0.02);
                this.vy = this.baseVy + turbY * 0.005;

                this.x += this.vx + (dx * mouseInfluence * 0.01);
                this.y += this.vy;

                // Store trail
                if (config.trailLength > 0) {
                    this.trail.unshift({ x: this.x, y: this.y, size: this.size, opacity: this.opacity });
                    if (this.trail.length > config.trailLength) {
                        this.trail.pop();
                    }
                }

                // Flicker effect (embers) - smooth breathing animation
                if (!config.blinkCycle) {
                    // Use multiple sine waves for organic, non-repetitive flicker
                    const flicker1 = Math.sin(time * this.flickerSpeed + this.flickerOffset);
                    const flicker2 = Math.sin(time * this.flickerSpeed * 0.7 + this.flickerOffset * 1.3);
                    const flicker = (flicker1 + flicker2 * 0.5) / 1.5; // Blend for smoother result

                    // Smooth interpolation toward target opacity
                    const targetOpacity = this.baseOpacity * (0.7 + flicker * 0.3);
                    this.opacity += (targetOpacity - this.opacity) * 0.05; // Smooth easing

                    const targetSize = this.baseSize * (0.9 + flicker * 0.1);
                    this.size += (targetSize - this.size) * 0.05; // Smooth easing
                }

                // Blink effect (fireflies)
                if (config.blinkCycle) {
                    this.nextBlink--;
                    if (this.nextBlink <= 0) {
                        this.isBlinking = true;
                        this.blinkIntensity = 0;
                    }

                    if (this.isBlinking) {
                        this.blinkPhase += this.blinkSpeed;
                        this.blinkIntensity = Math.sin(this.blinkPhase);

                        if (this.blinkIntensity < 0) {
                            this.isBlinking = false;
                            this.blinkPhase = 0;
                            this.blinkIntensity = 0;
                            this.nextBlink = 100 + Math.random() * 300;
                        }

                        this.opacity = this.baseOpacity * (0.1 + this.blinkIntensity * 0.9);
                        this.size = this.baseSize * (0.7 + this.blinkIntensity * 0.5);
                    } else {
                        this.opacity = this.baseOpacity * 0.1;
                        this.size = this.baseSize * 0.7;
                    }
                }

                // Fade out near top
                if (this.y < height * 0.2) {
                    this.life = this.y / (height * 0.2);
                    this.opacity *= this.life;
                }

                // Reset if off screen
                if (this.y < -50 || this.x < -50 || this.x > width + 50) {
                    this.reset();
                }
            }

            draw(ctx) {
                // Draw trail first (embers)
                if (config.trailLength > 0) {
                    for (let i = 0; i < this.trail.length; i++) {
                        const t = this.trail[i];
                        const trailOpacity = t.opacity * (1 - i / config.trailLength) * 0.5;
                        const trailSize = t.size * (1 - i / config.trailLength);

                        ctx.beginPath();
                        ctx.arc(t.x, t.y, trailSize, 0, Math.PI * 2);
                        ctx.fillStyle = `rgba(${this.color.r}, ${this.color.g}, ${this.color.b}, ${trailOpacity})`;
                        ctx.fill();
                    }
                }

                // Main glow (outer)
                const gradient = ctx.createRadialGradient(
                    this.x, this.y, 0,
                    this.x, this.y, this.size * config.glowIntensity
                );
                gradient.addColorStop(0, `rgba(${this.color.r}, ${this.color.g}, ${this.color.b}, ${this.opacity * 0.8})`);
                gradient.addColorStop(0.1, `rgba(${this.color.r}, ${this.color.g}, ${this.color.b}, ${this.opacity * 0.4})`);
                gradient.addColorStop(0.4, `rgba(${this.color.r}, ${this.color.g}, ${this.color.b}, ${this.opacity * 0.1})`);
                gradient.addColorStop(1, `rgba(${this.color.r}, ${this.color.g}, ${this.color.b}, 0)`);

                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size * config.glowIntensity, 0, Math.PI * 2);
                ctx.fillStyle = gradient;
                ctx.fill();

                // Bright core
                const coreGradient = ctx.createRadialGradient(
                    this.x, this.y, 0,
                    this.x, this.y, this.size
                );
                coreGradient.addColorStop(0, `rgba(255, 255, 255, ${this.opacity})`);
                coreGradient.addColorStop(0.3, `rgba(${this.color.r}, ${this.color.g}, ${this.color.b}, ${this.opacity})`);
                coreGradient.addColorStop(1, `rgba(${this.color.r}, ${this.color.g}, ${this.color.b}, 0)`);

                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
                ctx.fillStyle = coreGradient;
                ctx.fill();
            }
        }

        // Create particles for each layer
        config.layers.forEach((layer, layerIndex) => {
            for (let i = 0; i < layer.count; i++) {
                particles.push(new Particle(layer, layerIndex));
            }
        });

        // Stagger initial positions
        particles.forEach((p, i) => {
            p.y = Math.random() * height * 1.5;
        });

        // Animation loop
        let lastTime = 0;
        function animate(time) {
            const delta = time - lastTime;
            lastTime = time;

            // Clear with slight fade for motion blur effect
            ctx.fillStyle = 'rgba(0, 0, 0, 0.15)';
            ctx.fillRect(0, 0, width, height);

            // Clear completely for sharp rendering
            ctx.clearRect(0, 0, width, height);

            // Enable additive blending for glow
            ctx.globalCompositeOperation = 'lighter';

            // Update and draw particles (back to front for depth)
            particles.sort((a, b) => a.layerIndex - b.layerIndex);

            particles.forEach(particle => {
                particle.update(time);
                particle.draw(ctx);
            });

            // Reset composite operation
            ctx.globalCompositeOperation = 'source-over';

            requestAnimationFrame(animate);
        }

        // Start animation
        requestAnimationFrame(animate);

        console.log(`Cinematic ${mode} particle system initialized with ${particles.length} particles across ${config.layers.length} layers`);
    }

    // Initialize cinematic particle system
    initCinematicParticles(PARTICLE_MODE);
});
