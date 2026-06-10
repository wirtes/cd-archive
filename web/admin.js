const userForm = document.querySelector("#userForm");
const message = document.querySelector("#loginMessage");
const userList = document.querySelector("#userList");
const sessionUser = document.querySelector("#sessionUser");
const accountMenuButton = document.querySelector("#accountMenuButton");
const accountMenu = document.querySelector("#accountMenu");
const logoutButton = document.querySelector("#logoutButton");
const clearUserFormButton = document.querySelector("#clearUserForm");
const userFormTitle = document.querySelector("#userFormTitle");

let users = [];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

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
  if (!user.is_active) roles.push("inactive");
  if (user.is_admin) roles.push("admin");
  if (user.is_editor) roles.push("editor");
  return roles.join(", ") || "active viewer";
}

function resetUserForm(clearMessage = true) {
  userForm.reset();
  userForm.elements.username.readOnly = false;
  userForm.elements.is_active.checked = true;
  if (userFormTitle) userFormTitle.textContent = "Create User";
  if (clearMessage) message.textContent = "";
}

function editUser(username) {
  const user = users.find((item) => item.username === username);
  if (!user) return;
  userForm.elements.username.value = user.username;
  userForm.elements.username.readOnly = true;
  userForm.elements.password.value = "";
  userForm.elements.is_active.checked = Boolean(user.is_active);
  userForm.elements.is_admin.checked = Boolean(user.is_admin);
  userForm.elements.is_editor.checked = Boolean(user.is_editor);
  if (userFormTitle) userFormTitle.textContent = `Edit ${user.username}`;
  message.textContent = "Leave password blank to keep it unchanged.";
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
  users = payload.users || [];
  userList.innerHTML = `
    <h2>Users</h2>
    <ul>
      ${users
        .map(
          (user) => `
            <li>
              <strong>${escapeHtml(user.username)}</strong>
              <span>${escapeHtml(roleText(user))}</span>
              <button type="button" data-edit-user="${escapeAttribute(user.username)}">Edit</button>
            </li>
          `
        )
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
        is_active: Boolean(form.get("is_active")),
        is_admin: Boolean(form.get("is_admin")),
        is_editor: Boolean(form.get("is_editor")),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to save user.");
    resetUserForm(false);
    message.textContent = `${payload.username} saved.`;
    loadUsers();
  } catch (error) {
    message.textContent = error.message;
  }
});

userList.addEventListener("click", (event) => {
  const editButton = event.target.closest("[data-edit-user]");
  if (!editButton) return;
  editUser(editButton.dataset.editUser);
});

clearUserFormButton?.addEventListener("click", resetUserForm);

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
