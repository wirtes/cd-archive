const userForm = document.querySelector("#userForm");
const message = document.querySelector("#loginMessage");
const userList = document.querySelector("#userList");
const sessionUser = document.querySelector("#sessionUser");
const accountMenuButton = document.querySelector("#accountMenuButton");
const accountMenu = document.querySelector("#accountMenu");
const logoutButton = document.querySelector("#logoutButton");

function setAccountMenuOpen(open) {
  if (!accountMenuButton || !accountMenu) return;
  accountMenu.hidden = !open;
  accountMenuButton.setAttribute("aria-expanded", String(open));
}

function toggleAccountMenu() {
  setAccountMenuOpen(accountMenu?.hidden);
}

function roleText(user) {
  const roles = [];
  if (user.is_admin) roles.push("admin");
  if (user.is_editor) roles.push("editor");
  return roles.join(", ") || "viewer";
}

async function loadUsers() {
  const sessionResponse = await fetch("/api/session");
  if (sessionResponse.status === 401) {
    window.location.href = "/login.html?next=%2Fadmin.html";
    return;
  }
  const sessionPayload = await sessionResponse.json();
  if (sessionUser) sessionUser.textContent = sessionPayload.username || "";

  const response = await fetch("/api/users");
  if (response.status === 401) {
    window.location.href = "/login.html?next=%2Fadmin.html";
    return;
  }
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Unable to load users.");
  userList.innerHTML = `
    <h2>Users</h2>
    <ul>
      ${(payload.users || [])
        .map((user) => `<li><strong>${user.username}</strong><span>${roleText(user)}</span></li>`)
        .join("")}
    </ul>
  `;
}

userForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(userForm);
  message.textContent = "Saving user...";
  try {
    const response = await fetch("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: form.get("username"),
        password: form.get("password"),
        is_admin: Boolean(form.get("is_admin")),
        is_editor: Boolean(form.get("is_editor")),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to save user.");
    message.textContent = `${payload.username} saved.`;
    userForm.reset();
    loadUsers();
  } catch (error) {
    message.textContent = error.message;
  }
});

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  window.location.href = "/login.html?next=%2Fadmin.html";
}

accountMenuButton?.addEventListener("click", toggleAccountMenu);
logoutButton?.addEventListener("click", logout);
document.addEventListener("click", (event) => {
  if (!accountMenu || accountMenu.hidden) return;
  if (event.target.closest(".accountMenuWrap")) return;
  setAccountMenuOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setAccountMenuOpen(false);
});

loadUsers().catch((error) => {
  message.textContent = error.message;
});
