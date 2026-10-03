chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "SS_CLOSE_COLLECTOR" && sender.tab && sender.tab.id) {
    chrome.tabs.remove(sender.tab.id);
    sendResponse({ ok: true });
    return true;
  }

  if (msg && msg.type === "SS_GO_SHORT" && msg.url && sender.tab && sender.tab.id) {
    chrome.tabs.update(sender.tab.id, { url: msg.url }, () => {
      sendResponse({ ok: !chrome.runtime.lastError });
    });
    return true;
  }

  if (msg && msg.type === "SS_OPEN_COLLECTORS") {
    const queries = msg.queries || [];

    queries.forEach((q, i) => {
      setTimeout(() => {
        const url =
          "https://www.youtube.com/results?search_query=" +
          encodeURIComponent(q) +
          "&ssCollect=1&ssQuery=" +
          encodeURIComponent(q);

        chrome.tabs.create({ url, active: false }, tab => {
          if (!tab || !tab.id) return;

          // Safety close only after enough time for collection.
          setTimeout(() => {
            chrome.tabs.get(tab.id, existing => {
              if (!chrome.runtime.lastError && existing) chrome.tabs.remove(tab.id);
            });
          }, 14000);
        });
      }, i * 650);
    });

    sendResponse({ ok: true, count: queries.length });
    return true;
  }
});
