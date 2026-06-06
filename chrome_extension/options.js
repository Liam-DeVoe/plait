const DEFAULT_BASE_URL = "http://localhost:57381";
const input = document.getElementById("baseUrl");
const status = document.getElementById("status");

chrome.storage.sync.get("plaitBaseUrl").then(({ plaitBaseUrl }) => {
  input.value = plaitBaseUrl || DEFAULT_BASE_URL;
});

document.getElementById("save").addEventListener("click", async () => {
  const value = input.value.trim() || DEFAULT_BASE_URL;
  await chrome.storage.sync.set({ plaitBaseUrl: value });
  status.textContent = "Saved";
  setTimeout(() => (status.textContent = ""), 1500);
});
