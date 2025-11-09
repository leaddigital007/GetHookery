// Smooth scroll to form
function scrollToForm() {
    document.getElementById('contact').scrollIntoView({
        behavior: 'smooth'
    });
}

// Header scroll effect
window.addEventListener('scroll', function() {
    const header = document.querySelector('.header');
    if (window.scrollY > 100) {
        header.style.background = 'rgba(10, 10, 10, 0.98)';
        header.style.boxShadow = '0 2px 20px rgba(255, 107, 53, 0.2)';
    } else {
        header.style.background = 'rgba(10, 10, 10, 0.95)';
        header.style.boxShadow = 'none';
    }
});

// Form submission
document.getElementById('contactForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const formData = {
        name: document.getElementById('name').value,
        email: document.getElementById('email').value,
        website: document.getElementById('website').value,
        revenue: document.getElementById('revenue').value,
        message: document.getElementById('message').value
    };
    if (!formData.name || !formData.email || !formData.website || !formData.revenue || !formData.message) {
        alert('Please fill in all required fields to book your strategy call.');
        return;
    }
    const submitBtn = document.querySelector('.form-submit');
    const originalText = submitBtn.innerHTML;
    submitBtn.innerHTML = '<span>Sending...</span><i class="fas fa-spinner fa-spin"></i>';
    submitBtn.disabled = true;
    try {
        const res = await fetch('/api/contact', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            throw new Error(data && data.error ? data.error : 'Failed to submit');
        }
        submitBtn.innerHTML = '<span>Message Sent!</span><i class="fas fa-check"></i>';
        submitBtn.style.background = 'linear-gradient(135deg, #00C851 0%, #007E33 100%)';
        document.getElementById('contactForm').reset();
        // show success toast
        const successMessage = document.createElement('div');
        successMessage.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: linear-gradient(135deg, #00C851 0%, #007E33 100%);
            color: white;
            padding: 15px 25px;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0, 200, 81, 0.4);
            z-index: 10000;
            animation: slideInRight 0.5s ease;
        `;
        successMessage.innerHTML = '<i class="fas fa-check-circle"></i> Message sent! We will get back to you shortly.';
        document.body.appendChild(successMessage);
        setTimeout(() => successMessage.remove(), 3000);
    } catch (err) {
        alert('Submission failed: ' + err.message);
    } finally {
        setTimeout(() => {
            submitBtn.innerHTML = originalText;
            submitBtn.style.background = 'linear-gradient(135deg, #FF6B35 0%, #FF8C42 100%)';
            submitBtn.disabled = false;
        }, 1200);
    }
});

// Add CSS for slide animation
const style = document.createElement('style');
style.textContent = `
    @keyframes slideInRight {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
`;
document.head.appendChild(style);

// Add click events to CTA buttons that explicitly request contact scroll
document.querySelectorAll('.cta-btn[data-scroll="contact"]').forEach(btn => {
    btn.addEventListener('click', function() {
        scrollToForm();
    });
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

// Observe elements for animation
document.addEventListener('DOMContentLoaded', function() {
    const animatedElements = document.querySelectorAll('.service-card, .metric-card, .step, .benefit-item');
    
    animatedElements.forEach((el, index) => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(30px)';
        el.style.transition = `all 0.6s ease ${index * 0.1}s`;
        observer.observe(el);
    });
});

// Add hover effect to metric cards
document.querySelectorAll('.metric-card').forEach(card => {
    card.addEventListener('mouseenter', function() {
        this.style.transform = 'translateY(-10px) scale(1.02)';
    });
    
    card.addEventListener('mouseleave', function() {
        this.style.transform = 'translateY(-5px) scale(1)';
    });
});

// Interactive Cursor
const cursorGlow = document.querySelector('.cursor-glow');

document.addEventListener('mousemove', (e) => {
    if (cursorGlow) {
        cursorGlow.style.left = e.clientX + 'px';
        cursorGlow.style.top = e.clientY + 'px';
    }
});

// Enhanced cursor on interactive elements
document.querySelectorAll('button, a, .cta-btn').forEach(el => {
    el.addEventListener('mouseenter', () => {
        if (cursorGlow) {
            cursorGlow.style.transform = 'translate(-50%, -50%) scale(2)';
            cursorGlow.style.background = 'radial-gradient(circle, rgba(255, 165, 0, 0.5) 0%, rgba(255, 107, 53, 0.4) 50%, transparent 70%)';
        }
    });
    
    el.addEventListener('mouseleave', () => {
        if (cursorGlow) {
            cursorGlow.style.transform = 'translate(-50%, -50%) scale(1)';
            cursorGlow.style.background = 'radial-gradient(circle, rgba(255, 107, 53, 0.4) 0%, rgba(255, 140, 66, 0.3) 50%, transparent 70%)';
        }
    });
});

// Magnetic buttons effect
document.querySelectorAll('.cta-btn').forEach(btn => {
    btn.addEventListener('mousemove', (e) => {
        const rect = btn.getBoundingClientRect();
        const x = e.clientX - rect.left - rect.width / 2;
        const y = e.clientY - rect.top - rect.height / 2;
        
        const moveX = x * 0.15;
        const moveY = y * 0.15;
        
        btn.style.transform = `translate(${moveX}px, ${moveY}px)`;
    });
    
    btn.addEventListener('mouseleave', () => {
        btn.style.transform = 'translate(0px, 0px)';
    });
});

// Parallax effect for floating shapes
window.addEventListener('scroll', () => {
    const scrolled = window.pageYOffset;
    const shapes = document.querySelectorAll('.shape');
    
    shapes.forEach((shape, index) => {
        const speed = (index + 1) * 0.1;
        shape.style.transform = `translateY(${scrolled * speed}px)`;
    });
});

// Text typing animation for hero title
function typeWriter(element, text, speed = 50) {
    let i = 0;
    element.innerHTML = '';
    
    function type() {
        if (i < text.length) {
            element.innerHTML += text.charAt(i);
            i++;
            setTimeout(type, speed);
        }
    }
    type();
}

// Number counter animation
function animateCounter(element) {
    const target = parseFloat(element.getAttribute('data-target'));
    const prefix = element.getAttribute('data-prefix') || '';
    const suffix = element.getAttribute('data-suffix') || '%';
    const duration = 2000;
    const step = target / (duration / 16);
    let current = 0;
    
    const timer = setInterval(() => {
        current += step;
        if (current >= target) {
            current = target;
            clearInterval(timer);
        }
        
        const displayValue = target % 1 !== 0 ? current.toFixed(1) : Math.floor(current);
        element.textContent = prefix + displayValue + suffix;
    }, 16);
}

// Trigger counters when in view
const countersObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const counter = entry.target.querySelector('.metric-value');
            if (counter && !counter.classList.contains('animated')) {
                counter.classList.add('animated');
                setTimeout(() => animateCounter(counter), 200);
            }
        }
    });
}, { threshold: 0.5 });



document.addEventListener('DOMContentLoaded', function() {
    // Initialize counters
    document.querySelectorAll('.metric-card').forEach(card => {
        countersObserver.observe(card);
    });

    // Initialize typing effect
    const heroTitle = document.querySelector('.hero-title');
    if (heroTitle) {
        const originalText = heroTitle.textContent;
        setTimeout(() => {
            typeWriter(heroTitle, originalText, 30);
        }, 500);
    }
    
    // Initialize accordions
    document.querySelectorAll('.accordion').forEach(acc => {
        acc.querySelectorAll('.accordion-header').forEach(header => {
            header.addEventListener('click', () => {
                const item = header.closest('.accordion-item');
                const isOpen = item.classList.contains('open');
                // Close siblings
                acc.querySelectorAll('.accordion-item.open').forEach(openItem => {
                    if (openItem !== item) openItem.classList.remove('open');
                });
                // Toggle current
                if (!isOpen) {
                    item.classList.add('open');
                    header.setAttribute('aria-expanded', 'true');
                } else {
                    item.classList.remove('open');
                    header.setAttribute('aria-expanded', 'false');
                }
            });
        });
    });
    
    // Grid-scan canvas background for hero
    // Hero background scan disabled
});

// Console message for developers
console.log('%c🚀 GetHookery Agency', 'color: #667eea; font-size: 24px; font-weight: bold;');
console.log('%cWe build brands that convert! Visit https://gethookery.com', 'color: #666; font-size: 14px;'); 

// -------- Grid Scan Background --------
function initHeroGridScan() {
    const container = document.querySelector('.hero-bg');
    if (!container) return;
    
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const canvas = document.createElement('canvas');
    canvas.className = 'grid-scan-canvas';
    canvas.style.position = 'absolute';
    canvas.style.inset = '0';
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.pointerEvents = 'none';
    container.appendChild(canvas);
    container.classList.add('canvas-enabled');
    
    const ctx = canvas.getContext('2d');
    let width = 0, height = 0, dpr = Math.max(1, window.devicePixelRatio || 1);
    let grid = 14; // Grid Scale ~0.1 (small)
    const baseColor = { r: 255, g: 107, b: 53 }; // CTA orange
    const accentColor = { r: 255, g: 140, b: 66 };
    const lineWidth = 1; // Line Thickness
    const jitterAmp = 0.8; // Line Jitter (px)
    const scanSpeed = 80; // px/sec
    const scanSoftness = 120; // Scan Softness radius (px)
    const glowStrength = 0.5; // Scan Glow
    const noiseIntensity = 0.01;
    
    function resize() {
        const rect = container.getBoundingClientRect();
        width = Math.max(1, Math.floor(rect.width));
        height = Math.max(1, Math.floor(rect.height));
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        canvas.style.width = width + 'px';
        canvas.style.height = height + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener('resize', resize);
    
    function rgba(c, a) { return `rgba(${c.r}, ${c.g}, ${c.b}, ${a})`; }
    
    let last = performance.now();
    let scanY = 0;
    
    function draw(now) {
        const dt = (now - last) / 1000;
        last = now;
        scanY += scanSpeed * dt;
        if (scanY > height + scanSoftness) scanY = -scanSoftness;
        
        ctx.clearRect(0, 0, width, height);
        
        // optional subtle noise darkness
        if (noiseIntensity > 0) {
            ctx.fillStyle = `rgba(0,0,0,${noiseIntensity})`;
            ctx.fillRect(0, 0, width, height);
        }
        
        // Draw glow underlay using shadow
        ctx.save();
        ctx.shadowColor = rgba(accentColor, 0.35);
        ctx.shadowBlur = 12;
        ctx.lineWidth = lineWidth + 1.5;
        ctx.strokeStyle = rgba(accentColor, 0.15);
        drawGrid(scanY, true);
        ctx.restore();
        
        // Draw main grid
        ctx.lineWidth = lineWidth;
        ctx.strokeStyle = rgba(baseColor, 0.65);
        drawGrid(scanY, false);
        
        if (!prefersReduced) requestAnimationFrame(draw);
    }
    
    function drawGrid(scanCenter, isGlow) {
        // Horizontal lines
        for (let y = 0; y <= height; y += grid) {
            const dist = Math.abs(y - scanCenter);
            const alphaBoost = Math.exp(-(dist * dist) / (2 * scanSoftness * scanSoftness)) * (isGlow ? glowStrength : 1);
            const jitter = (Math.sin((y + last * 0.1)) * jitterAmp);
            ctx.globalAlpha = Math.min(1, 0.25 + alphaBoost);
            ctx.beginPath();
            ctx.moveTo(0, y + jitter);
            ctx.lineTo(width, y + jitter);
            ctx.stroke();
        }
        // Vertical lines
        for (let x = 0; x <= width; x += grid) {
            const dist = Math.abs((x / width) * height - scanCenter); // map x to comparable range
            const alphaBoost = Math.exp(-(dist * dist) / (2 * scanSoftness * scanSoftness)) * (isGlow ? glowStrength : 1);
            const jitter = (Math.cos((x + last * 0.1)) * jitterAmp);
            ctx.globalAlpha = Math.min(1, 0.22 + alphaBoost);
            ctx.beginPath();
            ctx.moveTo(x + jitter, 0);
            ctx.lineTo(x + jitter, height);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
    }
    
    requestAnimationFrame(draw);
}

// THREE.js shader-based grid scan (closer to reactbits)
function initHeroGridScanThree() {
    if (typeof THREE === 'undefined') return; // fallback to 2D version
    const container = document.querySelector('.hero-bg');
    if (!container) return;
    try {
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(dpr);
        renderer.setSize(container.clientWidth, container.clientHeight);
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.toneMapping = THREE.NoToneMapping;
        renderer.setClearColor(0x000000, 0);
        container.appendChild(renderer.domElement);
        container.classList.add('canvas-enabled');
        
        const scene = new THREE.Scene();
        const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
        const vert = `
        varying vec2 vUv;
        void main(){
          vUv = uv;
          gl_Position = vec4(position.xy, 0.0, 1.0);
        }`;
        const frag = `
        precision highp float;
        uniform vec3 iResolution;
        uniform float iTime;
        uniform vec2 uSkew;
        uniform float uTilt;
        uniform float uYaw;
        uniform float uLineThickness;
        uniform vec3 uLinesColor;
        uniform vec3 uScanColor;
        uniform float uGridScale;
        uniform float uLineStyle;
        uniform float uLineJitter;
        uniform float uScanOpacity;
        uniform float uScanDirection;
        uniform float uNoise;
        uniform float uBloomOpacity;
        uniform float uScanGlow;
        uniform float uScanSoftness;
        uniform float uPhaseTaper;
        uniform float uScanDuration;
        uniform float uScanDelay;
        varying vec2 vUv;
        float smoother01(float a, float b, float x){
          float t = clamp((x - a) / max(1e-5, (b - a)), 0.0, 1.0);
          return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
        }
        void main(){
          vec2 fragCoord = vUv * iResolution.xy;
          vec2 p = (2.0 * fragCoord - iResolution.xy) / iResolution.y;
          vec3 ro = vec3(0.0);
          vec3 rd = normalize(vec3(p, 2.0));
          float cR = cos(uTilt), sR = sin(uTilt);
          rd.xy = mat2(cR, -sR, sR, cR) * rd.xy;
          float cY = cos(uYaw), sY = sin(uYaw);
          rd.xz = mat2(cY, -sY, sY, cY) * rd.xz;
          vec2 skew = clamp(uSkew, vec2(-0.7), vec2(0.7));
          rd.xy += skew * rd.z;
          float gridScale = max(1e-5, uGridScale);
          vec3 color = vec3(0.0);
          float minT = 1e20;
          vec2 gridUV = vec2(0.0);
          float hitIsY = 1.0;
          for (int i = 0; i < 4; i++){
            float isY = float(i < 2);
            float pos = mix(-0.2, 0.2, float(i)) * isY + mix(-0.5, 0.5, float(i - 2)) * (1.0 - isY);
            float num = pos - (isY * 0.0 + (1.0 - isY) * 0.0);
            float den = isY * rd.y + (1.0 - isY) * rd.x;
            float t = num / den;
            vec3 h = ro + rd * t;
            float depthBoost = smoothstep(0.0, 3.0, h.z);
            h.xy += skew * 0.15 * depthBoost;
            bool use = t > 0.0 && t < minT;
            gridUV = use ? mix(h.zy, h.xz, isY) / gridScale : gridUV;
            minT = use ? t : minT;
            hitIsY = use ? isY : hitIsY;
          }
          vec3 hit = ro + rd * minT;
          float dist = length(hit - ro);
          float jitterAmt = clamp(uLineJitter, 0.0, 1.0);
          if (jitterAmt > 0.0) {
            vec2 j = vec2(
              sin(gridUV.y * 2.7 + iTime * 1.8),
              cos(gridUV.x * 2.3 - iTime * 1.6)
            ) * (0.15 * jitterAmt);
            gridUV += j;
          }
          float fx = fract(gridUV.x);
          float fy = fract(gridUV.y);
          float ax = min(fx, 1.0 - fx);
          float ay = min(fy, 1.0 - fy);
          float wx = fwidth(gridUV.x);
          float wy = fwidth(gridUV.y);
          float halfPx = max(0.0, uLineThickness) * 0.5;
          float tx = halfPx * wx;
          float ty = halfPx * wy;
          float aax = wx;
          float aay = wy;
          float lineX = 1.0 - smoothstep(tx, tx + aax, ax);
          float lineY = 1.0 - smoothstep(ty, ty + aay, ay);
          float primaryMask = max(lineX, lineY);
          vec2 gridUV2 = (hitIsY > 0.5 ? hit.xz : hit.zy) / gridScale;
          if (jitterAmt > 0.0) {
            vec2 j2 = vec2(
              cos(gridUV2.y * 2.1 - iTime * 1.4),
              sin(gridUV2.x * 2.5 + iTime * 1.7)
            ) * (0.15 * jitterAmt);
            gridUV2 += j2;
          }
          float fx2 = fract(gridUV2.x);
          float fy2 = fract(gridUV2.y);
          float ax2 = min(fx2, 1.0 - fx2);
          float ay2 = min(fy2, 1.0 - fy2);
          float wx2 = fwidth(gridUV2.x);
          float wy2 = fwidth(gridUV2.y);
          float tx2 = halfPx * wx2;
          float ty2 = halfPx * wy2;
          float aax2 = wx2;
          float aay2 = wy2;
          float lineX2 = 1.0 - smoothstep(tx2, tx2 + aax2, ax2);
          float lineY2 = 1.0 - smoothstep(ty2, ty2 + aay2, ay2);
          float altMask = max(lineX2, lineY2);
          float edgeDistX = min(abs(hit.x - (-0.5)), abs(hit.x - 0.5));
          float edgeDistY = min(abs(hit.y - (-0.2)), abs(hit.y - 0.2));
          float edgeDist = mix(edgeDistY, edgeDistX, hitIsY);
          float edgeGate = 1.0 - smoothstep(gridScale * 0.5, gridScale * 2.0, edgeDist);
          altMask *= edgeGate;
          float lineMask = max(primaryMask, altMask);
          float fadeStrength = 2.0;
          float fade = exp(-dist * fadeStrength);
          float dur = max(0.05, uScanDuration);
          float del = max(0.0, uScanDelay);
          float cycle = dur + del;
          float tCycle = mod(iTime, cycle);
          float scanPhase = clamp((tCycle - del) / dur, 0.0, 1.0);
          float phase = scanPhase;
          if (uScanDirection > 0.5 && uScanDirection < 1.5) {
            phase = 1.0 - phase;
          } else if (uScanDirection > 1.5) {
            float t2 = mod(max(0.0, iTime - del), 2.0 * dur);
            phase = (t2 < dur) ? (t2 / dur) : (1.0 - (t2 - dur) / dur);
          }
          float scanZMax = 2.0;
          float widthScale = max(0.1, uScanGlow);
          float sigma = max(0.001, 0.18 * widthScale * uScanSoftness);
          float sigmaA = sigma * 2.0;
          float scanZ = phase * scanZMax;
          float dz = abs(hit.z - scanZ);
          float lineBand = exp(-0.5 * (dz * dz) / (sigma * sigma));
          float headW = clamp(uPhaseTaper, 0.0, 0.49);
          float tailW = headW;
          float headFade = smoother01(0.0, headW, phase);
          float tailFade = 1.0 - smoother01(1.0 - tailW, 1.0, phase);
          float phaseWindow = headFade * tailFade;
          float pulseBase = lineBand * phaseWindow;
          float combinedPulse = pulseBase * clamp(uScanOpacity, 0.0, 1.0);
          float auraBand = exp(-0.5 * (dz * dz) / (sigmaA * sigmaA));
          float combinedAura = (auraBand * 0.25) * phaseWindow * clamp(uScanOpacity, 0.0, 1.0);
          float lineVis = lineMask;
          vec3 gridCol = uLinesColor * lineVis * fade;
          vec3 scanCol = uScanColor * combinedPulse;
          vec3 scanAura = uScanColor * combinedAura;
          vec3 col = gridCol + scanCol + scanAura;
          float n = fract(sin(dot(gl_FragCoord.xy + vec2(iTime * 123.4), vec2(12.9898,78.233))) * 43758.5453123);
          col += (n - 0.5) * uNoise;
          col = clamp(col, 0.0, 1.0);
          float alpha = clamp(max(lineVis, combinedPulse), 0.0, 1.0);
          gl_FragColor = vec4(col, alpha);
        }`;
        const uniforms = {
            iResolution: { value: new THREE.Vector3(container.clientWidth, container.clientHeight, dpr) },
            iTime: { value: 0 },
            uSkew: { value: new THREE.Vector2(0, 0) },
            uTilt: { value: 0 },
            uYaw: { value: 0 },
            uLineThickness: { value: 1.0 },
            uLinesColor: { value: new THREE.Color('#392e4e').convertSRGBToLinear() },
            uScanColor: { value: new THREE.Color('#FF9FFC').convertSRGBToLinear() },
            uGridScale: { value: 0.1 },
            uLineStyle: { value: 0.0 },
            uLineJitter: { value: 0.1 },
            uScanOpacity: { value: 0.4 },
            uScanDirection: { value: 2.0 }, // pingpong
            uNoise: { value: 0.01 },
            uBloomOpacity: { value: 0.6 },
            uScanGlow: { value: 0.5 },
            uScanSoftness: { value: 2.0 },
            uPhaseTaper: { value: 0.9 },
            uScanDuration: { value: 2.0 },
            uScanDelay: { value: 2.0 }
        };
        const material = new THREE.ShaderMaterial({
            uniforms,
            vertexShader: vert,
            fragmentShader: frag,
            transparent: true,
            depthWrite: false,
            depthTest: false
        });
        const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
        scene.add(quad);
        function onResize() {
            renderer.setSize(container.clientWidth, container.clientHeight);
            uniforms.iResolution.value.set(container.clientWidth, container.clientHeight, dpr);
        }
        window.addEventListener('resize', onResize);
        function render(t) {
            uniforms.iTime.value = t / 1000;
            renderer.render(scene, camera);
            requestAnimationFrame(render);
        }
        requestAnimationFrame(render);
    } catch (e) {
        // Fallback silently; 2D canvas remains
    }
}