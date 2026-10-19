const processedBridgeTabs = new Set();

function isYouTubeUrl(value) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      (url.hostname === "youtube.com" ||
        url.hostname.endsWith(".youtube.com")) &&
      (url.pathname === "/" || url.pathname === "/watch")
    );
  } catch {
    return false;
  }
}

async function handleBridgeTab(tabId, bridgeUrlValue) {
  let bridgeUrl;
  try {
    bridgeUrl = new URL(bridgeUrlValue);
  } catch {
    return;
  }
  if (
    bridgeUrl.origin !== "http://127.0.0.1:8765" ||
    bridgeUrl.pathname !== "/tab-bridge"
  ) {
    return;
  }

  if (processedBridgeTabs.has(tabId)) {
    return;
  }
  processedBridgeTabs.add(tabId);

  const action = bridgeUrl.searchParams.get("action") ?? "play";
  const requestId = bridgeUrl.searchParams.get("id");
  const targetUrl = bridgeUrl.searchParams.get("url");
  if (
    !requestId ||
    !["play", "pause", "resume"].includes(action) ||
    (action === "play" && (!targetUrl || !isYouTubeUrl(targetUrl)))
  ) {
    processedBridgeTabs.delete(tabId);
    return;
  }

  try {
    const youtubeTabs = await chrome.tabs.query({
      url: ["https://youtube.com/*", "https://*.youtube.com/*"]
    });
    const existingTab =
      youtubeTabs.find((tab) => tab.active) ?? youtubeTabs[0];

    if (action === "pause" || action === "resume") {
      let status = "no_youtube_tab";
      if (Number.isInteger(existingTab?.id)) {
        const [result] = await chrome.scripting.executeScript({
          target: { tabId: existingTab.id },
          func: async (playbackAction) => {
            const video = document.querySelector("video");
            if (!video) {
              return "no_video";
            }
            if (playbackAction === "pause") {
              video.pause();
              return "paused";
            }
            try {
              await video.play();
              return "playing";
            } catch {
              return "no_video";
            }
          },
          args: [action]
        });
        status = result?.result ?? "no_video";
      }

      try {
        const response = await fetch("http://127.0.0.1:8765/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: requestId, status })
        });
        if (!response.ok) {
          throw new Error(`Lucy bridge returned HTTP ${response.status}`);
        }
      } finally {
        await chrome.tabs.remove(tabId);
      }
    } else if (Number.isInteger(existingTab?.id)) {
      await chrome.tabs.update(existingTab.id, {
        url: targetUrl,
        active: true
      });
      await chrome.tabs.remove(tabId);
    } else {
      await chrome.tabs.update(tabId, {
        url: targetUrl,
        active: true
      });
    }
  } catch (error) {
    processedBridgeTabs.delete(tabId);
    throw error;
  }
}

function watchBridgeNavigation(tabId, bridgeUrl) {
  if (bridgeUrl?.startsWith("http://127.0.0.1:8765/tab-bridge?")) {
    void handleBridgeTab(tabId, bridgeUrl).catch((error) => {
      console.error("Lucy couldn't hand off the YouTube tab request.", error);
    });
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  watchBridgeNavigation(tabId, changeInfo.url ?? tab.url);
});

chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId === 0) {
    watchBridgeNavigation(details.tabId, details.url);
  }
});

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === "lucy-youtube-bridge" && sender.tab?.id) {
    watchBridgeNavigation(sender.tab.id, sender.url);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  processedBridgeTabs.delete(tabId);
});
