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
document.getElementById('contactForm').addEventListener('submit', function(e) {
    e.preventDefault();
    
    // Get form data
    const formData = {
        name: document.getElementById('name').value,
        email: document.getElementById('email').value,
        website: document.getElementById('website').value,
        revenue: document.getElementById('revenue').value,
        message: document.getElementById('message').value
    };
    
    // Enhanced validation
    if (!formData.name || !formData.email || !formData.website || !formData.revenue || !formData.message) {
        alert('Please fill in all required fields to book your strategy call.');
        return;
    }
    
    // Simulate form submission
    const submitBtn = document.querySelector('.form-submit');
    const originalText = submitBtn.innerHTML;
    
    submitBtn.innerHTML = '<span>Sending...</span><i class="fas fa-spinner fa-spin"></i>';
    submitBtn.disabled = true;
    
    // Simulate API call
    setTimeout(() => {
        submitBtn.innerHTML = '<span>Message Sent!</span><i class="fas fa-check"></i>';
        submitBtn.style.background = 'linear-gradient(135deg, #00C851 0%, #007E33 100%)';
        
        // Reset form
        document.getElementById('contactForm').reset();
        
        // Show success message
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
        successMessage.innerHTML = '<i class="fas fa-check-circle"></i> Strategy call booked! Check your email for next steps.';
        document.body.appendChild(successMessage);
        
        // Remove success message after 3 seconds
        setTimeout(() => {
            successMessage.remove();
        }, 3000);
        
        // Reset button after 2 seconds
        setTimeout(() => {
            submitBtn.innerHTML = originalText;
            submitBtn.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
            submitBtn.disabled = false;
        }, 2000);
        
    }, 1500);
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

// Add click events to all CTA buttons
document.querySelectorAll('.cta-btn').forEach(btn => {
    if (!btn.classList.contains('form-submit')) {
        btn.addEventListener('click', function() {
            scrollToForm();
        });
    }
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
});

// Console message for developers
console.log('%c🚀 GetHookery Agency', 'color: #667eea; font-size: 24px; font-weight: bold;');
console.log('%cWe build brands that convert! Visit https://gethookery.com', 'color: #666; font-size: 14px;'); 