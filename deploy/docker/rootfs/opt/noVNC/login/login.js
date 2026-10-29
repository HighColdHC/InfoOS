"use strict";
const form = document.getElementById("login-form");
const password = document.getElementById("password");
const button = document.getElementById("login-button");
const toggle = document.getElementById("toggle-password");
const status = document.getElementById("login-status");
function showError(message) {
  status.textContent = message;
  status.hidden = false;
}
if (document.cookie.split("; ").some(value => value === "login_result=INVALID_CREDENTIALS")) {
  showError("账户名或密码不正确，请重试。");
}
document.cookie = "login_result=; Max-Age=0; Path=/login; Secure; SameSite=Lax";
if (window.location.protocol !== "https:") {
  showError("请通过 HTTPS 安全连接登录。");
  button.disabled = true;
}
toggle.addEventListener("click", () => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  toggle.textContent = visible ? "隐藏" : "显示";
  toggle.setAttribute("aria-pressed", String(visible));
});
form.addEventListener("submit", event => {
  if (window.location.protocol !== "https:" || !form.checkValidity()) {
    event.preventDefault();
    return;
  }
  // Native URL-encoded POST: webauth owns cookies and success/failure redirects.
  button.disabled = true;
  button.textContent = "正在登录…";
  form.setAttribute("aria-busy", "true");
});
window.addEventListener("pageshow", () => {
  button.disabled = window.location.protocol !== "https:";
  button.textContent = "登录";
  form.removeAttribute("aria-busy");
  password.value = "";
});
