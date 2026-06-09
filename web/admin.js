const userForm = document.querySelector("#userForm");
const message = document.querySelector("#loginMessage");
const userList = document.querySelector("#userList");

function roleText(user) {
  const roles = [];
  if (user.is_admin) roles.push("admin");
  if (user.is_editor) roles.push("editor");
  return roles.join(", ") || "viewer";
}

async function loadUsers() {
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

loadUsers().catch((error) => {
  message.textContent = error.message;
});
