/**
 * API client for Therapist Finder backend.
 * @module api
 */

// Override via `window.API_BASE = 'https://your-api.onrender.com/api'` before
// this module loads (e.g. in a <script> tag in index.html) when the frontend
// is hosted on a different origin than the backend.
const API_BASE = (typeof window !== 'undefined' && window.API_BASE) || '/api';

/**
 * Custom error class for API errors.
 */
export class APIError extends Error {
    constructor(message, status, code = null) {
        super(message);
        this.name = 'APIError';
        this.status = status;
        this.code = code;
    }
}

/**
 * Make an API request with error handling.
 * @param {string} endpoint - API endpoint
 * @param {RequestInit} options - Fetch options
 * @returns {Promise<any>} - Response data
 */
async function request(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;

    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                ...options.headers,
            },
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new APIError(
                error.detail || `Request failed with status ${response.status}`,
                response.status,
                error.code
            );
        }

        // Handle empty responses
        const contentType = response.headers.get('content-type');
        if (contentType?.includes('application/json')) {
            return response.json();
        }

        return response;
    } catch (error) {
        if (error instanceof APIError) {
            throw error;
        }
        throw new APIError(
            error.message || 'Network error',
            0,
            'NETWORK_ERROR'
        );
    }
}

/**
 * Parse a PDF or text file to extract therapist data.
 * @param {File} file - The file to parse
 * @returns {Promise<Object>} - Parsed therapist data
 */
export async function parseFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    return request('/therapists/parse', {
        method: 'POST',
        body: formData,
    });
}

/**
 * Fetch a PDF from a psych-info.de URL and parse it server-side.
 * @param {string} url - https://psych-info.de/... PDF URL
 * @returns {Promise<Object>} - Parse response with therapists, total, with_email
 */
export async function parseUrl(url) {
    return request('/therapists/parse-url', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url }),
    });
}

/**
 * Live-crawl a therapist directory around an address, server-side.
 * @param {{source?: string, address: string, radiusKm: number, maxResults?: number, requireEmail?: boolean}} args
 * @returns {Promise<Object>} - Parse response with therapists, total, with_email
 */
export async function crawlSource({ source = 'psychotherapeutensuche', address, radiusKm, maxResults = 30, requireEmail = false }) {
    return request('/therapists/crawl', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            source,
            address,
            radius_km: radiusKm,
            max_results: maxResults,
            require_email: requireEmail,
        }),
    });
}

/**
 * Generate email drafts for therapists.
 * @param {Array} therapists - List of therapist objects
 * @param {Object} userInfo - User information
 * @param {string|null} [templateBody] - Optional custom template body
 * @returns {Promise<Object>} - Generated emails and files
 */
export async function generateEmails(therapists, userInfo, templateBody = null) {
    const payload = {
        therapists,
        user_info: userInfo,
    };
    if (templateBody != null) {
        payload.template_body = templateBody;
    }
    return request('/emails/generate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
    });
}

/**
 * Fetch the default email template body from the server.
 * @returns {Promise<{body: string}>}
 */
export async function getTemplate() {
    return request('/emails/template');
}

/**
 * Download a file from a blob URL.
 * @param {Blob} blob - File blob
 * @param {string} filename - Download filename
 */
export function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

/**
 * Download content as a file.
 * @param {string} content - File content
 * @param {string} filename - Download filename
 * @param {string} mimeType - MIME type
 */
export function downloadFile(content, filename, mimeType = 'text/plain') {
    const blob = new Blob([content], { type: mimeType });
    downloadBlob(blob, filename);
}

/**
 * Health check for the API.
 * @returns {Promise<Object>} - Health status
 */
export async function healthCheck() {
    return request('/health');
}

/**
 * Record that this browser opened a mail draft for the given therapist.
 * When `body` is provided, it is also appended to the sent_mails log so
 * future AI generations can avoid repeating phrasing.
 * @param {string} email
 * @param {string} browserId
 * @param {{body?: string, targetLang?: string}} [extras]
 * @returns {Promise<{recorded: boolean}>}
 */
export async function recordContact(email, browserId, extras = {}) {
    const payload = { email, browser_id: browserId };
    if (extras.body) payload.body = extras.body;
    if (extras.targetLang) payload.target_lang = extras.targetLang;
    return request('/contacts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

/**
 * Generate a therapist-inquiry mail body via the backend's LLM.
 * @param {{targetLang: string, insurance?: string|null, therapistEmails: string[], browserId: string}} args
 * @returns {Promise<{body: string}>}
 */
export async function aiGenerateMailBody({ targetLang, insurance, therapistEmails, browserId }) {
    return request('/emails/ai-generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            target_lang: targetLang,
            insurance: insurance ?? null,
            therapist_emails: therapistEmails || [],
            browser_id: browserId,
        }),
    });
}

/**
 * Fetch global contact counts for a list of therapist emails.
 * @param {string[]} emails
 * @returns {Promise<{counts: Record<string, number>}>}
 */
export async function getContactCounts(emails) {
    return request('/contacts/counts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emails }),
    });
}

/**
 * Fetch the emails this browser has already contacted.
 * @param {string} browserId
 * @returns {Promise<{emails: string[]}>}
 */
export async function getMyContacts(browserId) {
    const qs = new URLSearchParams({ browser_id: browserId });
    return request(`/contacts/mine?${qs}`);
}
