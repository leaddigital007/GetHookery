const express = require('express');
const path = require('path');
const nodemailer = require('nodemailer');

const app = express();
const PORT = process.env.PORT || 3000;

// Middlewares
app.use(express.json());
// Статические файлы
app.use(express.static(path.join(__dirname, 'public')));

// Основной маршрут
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Privacy Policy маршрут
app.get('/privacy.html', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'privacy.html'));
});

// API: Приём заявок с формы
app.post('/api/contact', async (req, res) => {
    try {
        const { name, email, website, revenue, message } = req.body || {};
        if (!name || !email || !website || !revenue || !message) {
            return res.status(400).json({ ok: false, error: 'Missing required fields' });
        }
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(email)) {
            return res.status(400).json({ ok: false, error: 'Invalid email' });
        }

        // Настройки транспорта почты через SMTP (рекомендуется задать в переменных окружения)
        const {
            SMTP_HOST,
            SMTP_PORT,
            SMTP_SECURE,
            SMTP_USER,
            SMTP_PASS,
            CONTACT_TO,
            CONTACT_FROM
        } = process.env;
        // Fallbacks for MailerToGo add-on variables
        const smtpHost = SMTP_HOST || process.env.MAILERTOGO_SMTP_HOST;
        const smtpPort = Number(SMTP_PORT || process.env.MAILERTOGO_SMTP_PORT || 587);
        const smtpUser = SMTP_USER || process.env.MAILERTOGO_SMTP_USER;
        const smtpPass = SMTP_PASS || process.env.MAILERTOGO_SMTP_PASSWORD;
        const smtpSecure =
            String(SMTP_SECURE ?? (smtpPort === 465)).toLowerCase() === 'true' || smtpPort === 465;

        if (!smtpHost || !smtpPort || !smtpUser || !smtpPass || !CONTACT_TO) {
            // Без настроенного транспорта — не валим запрос, а логируем как "приём без отправки"
            console.warn('Email transport is not configured. Set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/CONTACT_TO env vars.');
            console.info('Contact form payload:', { name, email, website, revenue, message });
            return res.status(202).json({ ok: true, dryRun: true });
        }

        const transporter = nodemailer.createTransport({
            host: smtpHost,
            port: smtpPort,
            secure: smtpSecure,
            auth: { user: smtpUser, pass: smtpPass }
        });

        const htmlBody = `
            <h2>New Contact Form Submission</h2>
            <ul>
                <li><strong>Name:</strong> ${escapeHtml(name)}</li>
                <li><strong>Email:</strong> ${escapeHtml(email)}</li>
                <li><strong>Website/Business:</strong> ${escapeHtml(website)}</li>
                <li><strong>Revenue:</strong> ${escapeHtml(revenue)}</li>
            </ul>
            <p><strong>Message:</strong></p>
            <p>${escapeHtml(message).replace(/\\n/g, '<br/>')}</p>
        `;

        await transporter.sendMail({
            from: CONTACT_FROM || 'GetHookery Website <no-reply@gethookery.com>',
            to: CONTACT_TO,
            subject: 'New lead from GetHookery website',
            replyTo: email,
            html: htmlBody
        });

        return res.json({ ok: true });
    } catch (err) {
        console.error('Contact form error:', err);
        return res.status(500).json({ ok: false, error: 'Internal error' });
    }
});

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Запуск сервера
app.listen(PORT, () => {
    console.log(`🚀 GetHookery Agency running on port ${PORT}`);
}); 