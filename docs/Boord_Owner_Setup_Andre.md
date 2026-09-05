# Boord Owner — Setting It Up on Your Computer

This is a once-off setup. When it's done, Boord Owner sits on your desktop like any other program: one click and the farm's figures are in front of you, from home, the office, or anywhere with internet.

There are two pieces to it:

- **Tailscale** — a small free program that puts your computer on the farm's own private network. Nothing of yours is shared; it simply lets your computer reach the farm server and nobody else's computer reach it.
- **Boord Owner** — the dashboard itself. It runs on the farm server and opens in your web browser. There is nothing to install for it, and no password to remember.

Set up Tailscale first. The dashboard will not open until it is connected.

You will need about ten minutes, and the invitation email from the farm office (see Step 2).

---

## Step 1 — Install Tailscale

**On a Windows PC:**

1. Open your browser and go to **tailscale.com/download/windows**.
2. Click **Download Tailscale for Windows**. The file lands in your Downloads folder.
3. Double-click the downloaded file to run it, and click through the installer. Say **Yes** if Windows asks for permission to make changes.
4. When it finishes, a small Tailscale icon appears near the clock, at the bottom-right of your screen. You may have to click the little ^ arrow there to see it.

If Windows puts up a blue **"Windows protected your PC"** box, click **More info** and then **Run anyway** — that message only means the file came from the internet, which it did.

**On a Mac:** open the **App Store**, search for **Tailscale**, and click **Get**. Everything after this works the same.

**On a phone or tablet:** install **Tailscale** from the App Store or Google Play. The rest of these steps are the same on all of them, and it is worth doing on your phone too once the computer is working.

---

## Step 2 — Join the farm's network

Your computer now has Tailscale, but it is not yet on the farm's network. The farm office sends you an invitation to join it.

1. **Ask the farm office to send you a Tailscale invitation** for the farm server. They do this from their Tailscale admin page, using the **Share** option on the server, sent to your email address.
2. Open that email and click the link in it. It opens a Tailscale page in your browser.
3. Sign in when it asks. Use whichever account is easiest for you — Google, Microsoft, or Apple. There is no new password to create.
4. Click **Accept** to accept the shared machine.
5. Go back to the Tailscale icon near your clock and click it. It should say **Connected**, and the farm server should be listed by name.

If the farm office instead gives you the login details for the farm's own Tailscale account, sign in with those in the Tailscale program and you are on the network straight away — you can skip the invitation.

**Leave Tailscale switched on.** It starts by itself when you switch the computer on and runs quietly in the background. It does not slow anything down and it does not affect the rest of your internet.

---

## Step 3 — Open Boord Owner

With Tailscale connected, open your browser and go to the farm's own address, which looks like this:

```
https://<server-name>.<tailnet-name>.ts.net:8443/
```

**The farm office will give you the exact address to use** — the two bracketed parts are the farm server's name and the farm's Tailscale network name, and they are the same every time. Write it down once; Step 4 means you never type it again.

The dashboard opens straight away. There is no sign-in.

Two things about that address are worth knowing:

- **The `:8443` on the end matters.** The farm office's own Boord app answers on the same name *without* it. If you leave the port off, you land on their app instead of yours, and it will look wrong or say **"Not Found"**.
- **Type it once, then never again** — Step 4 puts it on your desktop.

---

## Step 4 — Put it on your desktop

This turns the web page into what looks and behaves like an ordinary program, in its own window with its own icon.

**In Google Chrome:**

1. Open the address above.
2. Click the **three dots** at the top-right of Chrome.
3. Choose **Cast, save and share**, then **Install page as app**.
4. Name it **Boord Owner** and click **Install**.

**In Microsoft Edge:**

1. Open the address above.
2. Click the **three dots** at the top-right of Edge.
3. Choose **Apps**, then **Install this site as an app**.
4. Name it **Boord Owner** and click **Install**.

Either way you end up with a Boord Owner icon on your desktop and in the Start menu. Right-click that icon and choose **Pin to taskbar** to keep it along the bottom of the screen.

**Don't want to install anything?** Just press **Ctrl+D** on the page to bookmark it. That works fine; it simply opens inside your browser with the rest of your tabs.

**On a phone or tablet:** open the address in the browser, then use **Share → Add to Home Screen**. It gets its own icon alongside your other apps.

---

## What you will see

Across the top: the farm name, today's date and time, and the current weather. Below that, four tabs:

| Tab | What it answers |
| --- | --- |
| **Dashboard** | What is happening right now, for any date range you pick |
| **Analysis** | How this season is tracking against 2020–2025 |
| **Weather** | What the weather has actually done here, 1987 to today |
| **Risk** | How risky this season's weather looks, and what that means for the harvest |

Everything is **read-only**. Nothing you tap can change a figure or break anything — the numbers come from the farm office's Boord app and are shown as they stand.

---

## If something does not work

| What you see | What it means |
| --- | --- |
| The page will not load at all, or spins forever | Tailscale is not connected. Click the Tailscale icon near the clock and check it says **Connected**. This is by far the most common cause. |
| **"Not Found"**, or a page that looks like the wrong app | The `:8443` is missing from the address, or mistyped. Check it against Step 3. |
| A certificate or **"not private"** warning | The address is misspelled. Compare it letter by letter with Step 3. |
| The page loads but the figures look frozen or wrong | The farm server may be switched off, or the app is not running. Ring the farm office. |
| Tailscale says **"Logged out"** | Click the icon and sign in again with the same account you used in Step 2. |

---

## Two things worth knowing

**There is no password.** The dashboard opens for anybody who can reach it, and being on the farm's Tailscale network is the only thing standing in front of it. That is a deliberate choice for a farm this size, and it means a computer or phone with Tailscale on it should be treated like an unlocked filing cabinet.

**If a device is lost, stolen, or sold, tell the farm office.** Removing that device from Tailscale is the only way to cut off its access — there is no account to disable and no password to change.

---

*Any trouble with the setup, the farm office can walk you through it.*
