const $ = id => document.getElementById(id);

const CATEGORIES = [
  {id:"education", label:"Education", keywords:["education","study","learning","science","math","course"]},
  {id:"technology", label:"Technology", keywords:["python","coding","programming","java","javascript","ai","software"]},
  {id:"islamic", label:"Islamic", keywords:["quran","islam","hadith","dua","nasheed","recitation"]},
  {id:"fitness", label:"Fitness", keywords:["gym","fitness","workout","exercise","health"]},
  {id:"motivation", label:"Motivation", keywords:["motivation","discipline","success","productive"]},
  {id:"kids", label:"Kids Learning", keywords:["kids","poem","phonics","abc","nursery rhyme"]},
  {id:"gaming", label:"Gaming", keywords:["gaming","gameplay","minecraft","roblox","pubg"]},
  {id:"music", label:"Music", keywords:["music","song","lyrics","remix","singer"]},
  {id:"sports", label:"Sports", keywords:["cricket","football","sports","match"]},
  {id:"comedy", label:"Comedy", keywords:["funny","comedy","meme","prank"]}
];

let state = {
  mode:"testing",
  interests:"",
  categories:["education","technology","islamic","fitness","motivation","kids"],
  timeSlots:[]
};

function renderCategories(){
  const selected = new Set(state.categories || []);
  $("categoryList").innerHTML = CATEGORIES.map(cat => `
    <label class="cat">
      <input type="checkbox" data-cat="${cat.id}" ${selected.has(cat.id) ? "checked" : ""}>
      <span><b>${cat.label}</b><small>${cat.keywords.slice(0,5).join(", ")}</small></span>
    </label>
  `).join("");
}

function renderSlots(){
  const slots = state.timeSlots || [];
  $("slotList").innerHTML = slots.map((s,i) => `
    <div class="slot">
      <span><b>${s.start} - ${s.end}</b><br>${s.keywords.join(", ")}</span>
      <button data-del-slot="${i}">Delete</button>
    </div>
  `).join("");

  document.querySelectorAll("[data-del-slot]").forEach(btn => {
    btn.onclick = () => {
      state.timeSlots.splice(Number(btn.dataset.delSlot), 1);
      renderSlots();
    };
  });
}

function render(){
  $("interests").value = state.interests || "";
  const m = document.querySelector(`input[name="mode"][value="${state.mode || "testing"}"]`);
  if(m) m.checked = true;
  renderCategories();
  renderSlots();
}

function collect(){
  state.mode = document.querySelector("input[name='mode']:checked")?.value || "testing";
  state.interests = $("interests").value.trim();
  state.categories = Array.from(document.querySelectorAll("[data-cat]:checked")).map(x => x.dataset.cat);
}

function setStatus(msg){
  $("status").textContent = msg;
  setTimeout(() => $("status").textContent = "", 2500);
}

function activeSlotKeywords(){
  const now = new Date();
  const mins = now.getHours() * 60 + now.getMinutes();

  return (state.timeSlots || []).filter(slot => {
    if(!slot.start || !slot.end) return false;
    const [sh,sm] = slot.start.split(":").map(Number);
    const [eh,em] = slot.end.split(":").map(Number);
    const start = sh * 60 + sm;
    const end = eh * 60 + em;
    return start <= end ? (mins >= start && mins <= end) : (mins >= start || mins <= end);
  }).flatMap(slot => slot.keywords || []);
}

function poolQueries(){
  collect();
  const queries = [];

  const add = q => {
    q = String(q || "").trim();
    if(q && !queries.some(x => x.toLowerCase() === q.toLowerCase())) queries.push(q);
  };

  // User direct interests
  (state.interests || "").split(",").map(x => x.trim()).filter(Boolean).forEach(p => {
    add(`${p} shorts`);
    add(`${p} youtube shorts`);
    add(`${p} short video`);
  });

  // Current active time slot gets priority
  activeSlotKeywords().forEach(p => {
    add(`${p} shorts`);
    add(`${p} youtube shorts`);
  });

  // Selected categories also build pool
  const selected = new Set(state.categories || []);
  CATEGORIES.forEach(cat => {
    if(selected.has(cat.id)){
      add(`${cat.label} shorts`);
      add(`${cat.keywords.slice(0,3).join(" ")} shorts`);
    }
  });

  return queries.slice(0, 16);
}

async function save(){
  collect();

  const queries = poolQueries();

  chrome.storage.sync.set({ ssPoolSettings: state }, () => {
    chrome.storage.local.set({
      ssPool: [],
      ssPoolIndex: 0,
      ssPoolRound: Date.now(),
      ssCollector: { active:true, expected: queries.length, done:0 }
    }, () => {
      chrome.runtime.sendMessage({ type:"SS_OPEN_COLLECTORS", queries }, () => {});
      setStatus("Saved. Building pool...");
    });
  });
}

$("addSlot").onclick = () => {
  const start = $("slotStart").value;
  const end = $("slotEnd").value;
  const keywords = $("slotKeywords").value.split(",").map(x => x.trim()).filter(Boolean);

  if(!start || !end || !keywords.length){
    setStatus("Add time and keywords.");
    return;
  }

  state.timeSlots.push({start,end,keywords});
  $("slotStart").value = "";
  $("slotEnd").value = "";
  $("slotKeywords").value = "";
  renderSlots();
};

$("save").onclick = save;

chrome.storage.sync.get(["ssPoolSettings"], res => {
  state = { ...state, ...(res.ssPoolSettings || {}) };
  render();
});
