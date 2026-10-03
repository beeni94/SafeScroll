/* SafeScroll Pool-Only Final
   Logic:
   - User interests build a pool.
   - User sees only pool Shorts.
   - When pool becomes low, extension refreshes pool automatically.
*/

(function collector(){
  try{
    const p = new URLSearchParams(location.search);
    if(p.get("ssCollect") !== "1") return;

    const query = p.get("ssQuery") || "interest";

    function collectItems(){
      const map = new Map();

      function add(id, title){
        if(!id || map.has(id)) return;
        map.set(id, { videoId:id, title:title || query, query, addedAt:Date.now() });
      }

      document.querySelectorAll("a[href*='/shorts/']").forEach(a => {
        const href = a.href || a.getAttribute("href") || "";
        const m = href.match(/\/shorts\/([^?&#/]+)/);
        if(m) add(m[1], a.getAttribute("title") || a.textContent || query);
      });

      const html = document.documentElement.innerHTML || "";
      const re = /\/shorts\/([a-zA-Z0-9_-]{8,})/g;
      let m;
      while((m = re.exec(html)) && map.size < 50){
        add(m[1], query);
      }

      return [...map.values()].slice(0, 35);
    }

    function mergePool(items){
      chrome.storage.local.get(["ssPool","ssCollector"], res => {
        const byId = new Map();
        (res.ssPool || []).concat(items).forEach(item => {
          if(item && item.videoId && !byId.has(item.videoId)){
            byId.set(item.videoId, item);
          }
        });

        const collector = res.ssCollector || {};
        collector.done = (collector.done || 0) + 1;
        if(collector.expected && collector.done >= collector.expected){
          collector.active = false;
        }

        chrome.storage.local.set({
          ssPool: [...byId.values()],
          ssPoolIndex: 0,
          ssCollector: collector
        }, () => {
          chrome.runtime.sendMessage({ type:"SS_CLOSE_COLLECTOR" });
        });
      });
    }

    let n = 0;
    const t = setInterval(() => {
      window.scrollTo(0, document.body.scrollHeight);
      n++;
      if(n >= 5) clearInterval(t);
    }, 800);

    setTimeout(() => mergePool(collectItems()), 6200);
  }catch(e){}
})();

let settings = { mode:"testing", interests:"", categories:[], timeSlots:[] };
let pool = [];
let poolIndex = 0;
let poolIds = new Set();
let lastNavigateAt = 0;
let badge = null;
let shield = null;
let refreshInProgress = false;

const LOW_POOL_THRESHOLD = 6;

function isShorts(){
  return location.href.includes("/shorts/");
}

function currentId(){
  const m = location.href.match(/\/shorts\/([^?&#/]+)/);
  return m ? m[1] : "";
}


function showShield(msg){
  if(!shield){
    shield = document.createElement("div");
    shield.id = "ss-pool-shield";
    document.documentElement.appendChild(shield);
  }
  shield.textContent = msg || "Loading clean Short...";
  shield.style.display = "flex";
}

function hideShield(){
  if(shield){
    shield.style.display = "none";
  }
}

function show(type, msg){
  if(settings.mode === "silent") return;

  if(!badge){
    badge = document.createElement("div");
    badge.id = "ss-pool-badge";
    document.documentElement.appendChild(badge);
  }

  badge.textContent = msg;
  badge.style.background = type === "pool" ? "#2563eb" : type === "warn" ? "#dc2626" : "#6b7280";
}

function currentSlotKeywords(){
  const now = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();

  return (settings.timeSlots || []).filter(slot => {
    if(!slot.start || !slot.end) return false;
    const [sh,sm] = slot.start.split(":").map(Number);
    const [eh,em] = slot.end.split(":").map(Number);
    const start = sh * 60 + sm;
    const end = eh * 60 + em;
    return start <= end ? (mins >= start && mins <= end) : (mins >= start || mins <= end);
  }).flatMap(slot => slot.keywords || []);
}

function interestQueries(){
  const categoryMap = {
    education:["education","study learning","science math"],
    technology:["python coding","java programming","ai software"],
    islamic:["quran recitation","islamic nasheed","hadith dua"],
    fitness:["gym workout","fitness exercise"],
    motivation:["motivation discipline","success productive"],
    kids:["kids poem","phonics abc"],
    gaming:["gaming gameplay","minecraft roblox"],
    music:["music song","lyrics remix"],
    sports:["cricket football","sports match"],
    comedy:["funny comedy","meme prank"]
  };

  const queries = [];
  const add = q => {
    q = String(q || "").trim();
    if(q && !queries.some(x => x.toLowerCase() === q.toLowerCase())) queries.push(q);
  };

  (settings.interests || "").split(",").map(x => x.trim()).filter(Boolean).forEach(p => {
    add(`${p} shorts`);
    add(`${p} youtube shorts`);
    add(`${p} short video`);
  });

  currentSlotKeywords().forEach(p => {
    add(`${p} shorts`);
    add(`${p} youtube shorts`);
  });

  (settings.categories || []).forEach(cat => {
    (categoryMap[cat] || []).forEach(p => add(`${p} shorts`));
  });

  return queries.slice(0, 16);
}

function refreshPoolIfNeeded(force=false){
  if(refreshInProgress) return;
  if(!settings.interests) return;

  const remaining = pool.length - poolIndex;
  if(!force && pool.length > 0 && remaining > LOW_POOL_THRESHOLD) return;

  refreshInProgress = true;
  const queries = interestQueries();

  chrome.storage.local.set({
    ssCollector: { active:true, expected: queries.length, done:0 }
  }, () => {
    chrome.runtime.sendMessage({ type:"SS_OPEN_COLLECTORS", queries }, () => {
      show("pool", "REFRESHING POOL...");
      setTimeout(() => { refreshInProgress = false; }, 8000);
    });
  });
}

function nextPool(){
  if(!pool.length) return null;

  const current = currentId();

  for(let i=0; i<pool.length; i++){
    const item = pool[poolIndex % pool.length];
    poolIndex = (poolIndex + 1) % pool.length;

    if(item && item.videoId && item.videoId !== current){
      chrome.storage.local.set({ ssPoolIndex: poolIndex });
      return item;
    }
  }

  return null;
}

function goToPool(){
  if(!isShorts()) return;

  if(!pool.length){
    showShield("Pool is building...<br><small>Please wait while SafeScroll prepares clean Shorts</small>");
    show("warn", "POOL BUILDING...");
    refreshPoolIfNeeded(true);
    return;
  }

  const current = currentId();

  // If current Short is already from pool, allow it and remove shield.
  if(current && poolIds.has(current)){
    hideShield();
    show("pool", `POOL SHORT ✅ ${poolIndex}/${pool.length}`);
    refreshPoolIfNeeded(false);
    return;
  }

  if(Date.now() - lastNavigateAt < 700) return;
  lastNavigateAt = Date.now();

  const item = nextPool();

  showShield("Loading clean Short...");

  if(!item){
    show("warn", "POOL ENDED · REFRESHING...");
    refreshPoolIfNeeded(true);
    return;
  }

  const target = "https://www.youtube.com/shorts/" + item.videoId;
  showShield("Loading clean Short...");
  show("pool", `SHOWING POOL SHORT ${poolIndex}/${pool.length}`);

  try{
    chrome.runtime.sendMessage({ type:"SS_GO_SHORT", url:target }, res => {
      if(chrome.runtime.lastError || !res || !res.ok){
        location.assign(target);
      }
    });
  }catch(e){
    location.assign(target);
  }

  setTimeout(() => {
    if(!location.href.includes("/shorts/" + item.videoId)){
      location.href = target;
    }
  }, 400);
}

function load(){
  chrome.storage.sync.get(["ssPoolSettings"], res => {
    settings = { ...settings, ...(res.ssPoolSettings || {}) };

    chrome.storage.local.get(["ssPool","ssPoolIndex"], data => {
      pool = data.ssPool || [];
      poolIndex = Number(data.ssPoolIndex || 0);
      poolIds = new Set(pool.map(x => x.videoId).filter(Boolean));

      if(isShorts()){
        if(!currentId() || !poolIds.has(currentId())){
          showShield("Loading clean Short...");
        }
        if(!pool.length){
          showShield("Pool is building...<br><small>Please wait while SafeScroll prepares clean Shorts</small>");
          refreshPoolIfNeeded(true);
        }
        setTimeout(goToPool, 50);
        setTimeout(goToPool, 250);
        setTimeout(goToPool, 800);
      }
    });
  });
}

chrome.storage.onChanged.addListener((changes, area) => {
  if(area === "sync" && changes.ssPoolSettings){
    settings = { ...settings, ...(changes.ssPoolSettings.newValue || {}) };
    refreshPoolIfNeeded(true);
    setTimeout(goToPool, 300);
  }

  if(area === "local"){
    if(changes.ssPool){
      pool = changes.ssPool.newValue || [];
      poolIds = new Set(pool.map(x => x.videoId).filter(Boolean));
      if(pool.length && typeof hideShield === "function") hideShield();
      show("pool", `POOL READY: ${pool.length}`);
      setTimeout(goToPool, 300);
    }

    if(changes.ssPoolIndex){
      poolIndex = Number(changes.ssPoolIndex.newValue || 0);
    }
  }
});

document.addEventListener("yt-navigate-start", () => {
  if(isShorts() && (!currentId() || !poolIds.has(currentId()))){
    showShield("Loading clean Short...");
  }
});

document.addEventListener("yt-navigate-finish", () => {
  if(isShorts() && (!currentId() || !poolIds.has(currentId()))){
    showShield("Loading clean Short...");
  }
  setTimeout(goToPool, 50);
  setTimeout(goToPool, 200);
  setTimeout(goToPool, 700);
});

["scroll","wheel","touchmove"].forEach(ev => {
  window.addEventListener(ev, () => {
    setTimeout(goToPool, 100);
    setTimeout(goToPool, 450);
  }, {passive:true});
});

window.addEventListener("keydown", () => {
  setTimeout(goToPool, 100);
  setTimeout(goToPool, 450);
}, true);

setInterval(goToPool, 250);
setInterval(() => refreshPoolIfNeeded(false), 5000);

load();


// Auto time-slot pool rebuild
function getActiveSlotKey(slots){
  const now=new Date();
  const mins=now.getHours()*60+now.getMinutes();
  for(const slot of (slots||[])){
    if(!slot.start||!slot.end) continue;
    const [sh,sm]=slot.start.split(":").map(Number);
    const [eh,em]=slot.end.split(":").map(Number);
    const start=sh*60+sm,end=eh*60+em;
    const active=start<=end?(mins>=start&&mins<=end):(mins>=start||mins<=end);
    if(active) return slot.start+"-"+slot.end+"|"+(slot.keywords||[]).join(",");
  }
  return "none";
}

let ssLastSlotKey=null;

setInterval(()=>{
  chrome.storage.sync.get(["ssPoolSettings"],res=>{
    const s=res.ssPoolSettings||{};
    const key=getActiveSlotKey(s.timeSlots||[]);

    if(ssLastSlotKey===null){
      ssLastSlotKey=key;
      return;
    }

    if(key!==ssLastSlotKey){
      ssLastSlotKey=key;

      chrome.storage.local.set({
        ssPool:[],
        ssPoolIndex:0,
        ssPoolRound:Date.now()
      });

      location.reload();
    }
  });
},30000);

