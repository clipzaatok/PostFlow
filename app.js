const CLIENT_KEY = "YOUR_TIKTOK_CLIENT_KEY";
const REDIRECT_URI = "https://clipzaatok.github.io/PostFlow/auth/callback.html";

function loginWithTikTok() {
    const url =
      `https://www.tiktok.com/v2/auth/authorize/` +
      `?client_key=${CLIENT_KEY}` +
      `&scope=user.info.basic` +
      `&response_type=code` +
      `&redirect_uri=${encodeURIComponent(REDIRECT_URI)}`;

    window.location.href = url;
}
