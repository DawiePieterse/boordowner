// Build config for frontend/shared/tailwind.css - see README "Frontend
// styles". The page used to ship Tailwind's 400 KB runtime (shared/tailwind.js)
// and compile its classes in the browser on every open; this precompiles
// exactly the classes the app's HTML/JS use instead.
module.exports = {
  content: ["./index.html", "./owner.js", "./shared/*.js"],
  theme: { extend: {} },
  plugins: [],
};
