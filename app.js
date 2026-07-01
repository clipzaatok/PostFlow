const CLIENT_KEY = "YOUR_TIKTOK_CLIENT_KEY";
const REDIRECT_URI = "https://clipzaatok.github.io/PostFlow/auth/callback.html";

// generate CSRF protection string
const state = Math.random().toString(36).substring(2);

sessionStorage.setItem("tiktok_oauth_state", state);

function loginWithTikTok() {
    const url =
      `https://www.tiktok.com/v2/auth/authorize/` +
      `?client_key=${CLIENT_KEY}` +
      `&scope=user.info.basic` +
      `&response_type=code` +
      `&redirect_uri=${encodeURIComponent(REDIRECT_URI)}` +
      `&state=${state}`;

    window.location.href = url;
}
