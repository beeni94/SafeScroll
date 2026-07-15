const form=document.getElementById('registerForm');
if(form){
  form.addEventListener('submit',e=>{
    e.preventDefault();
    const p=document.getElementById('password').value;
    const c=document.getElementById('confirm').value;
    const err=document.getElementById('formError');
    const strong=/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
    if(!strong.test(p)){err.textContent='Use 8+ characters with uppercase, lowercase, number and symbol.';document.getElementById('password').focus();return;}
    if(p!==c){err.textContent='Passwords do not match.';document.getElementById('confirm').focus();return;}
    err.style.color='#0d9488';err.textContent='UI validation passed. Backend connection comes in Sprint 2.';
  });
}


document.querySelectorAll('.eye-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    const input=btn.parentElement.querySelector('input');
    const showing=input.type==='text';
    input.type=showing?'password':'text';
    btn.textContent=showing?'👁':'🙈';
    btn.setAttribute('aria-label',showing?'Show password':'Hide password');
  });
});

const passwordInput=document.getElementById('password');
const strengthFill=document.getElementById('strengthFill');
const strengthText=document.getElementById('strengthText');

if(passwordInput && strengthFill && strengthText){
  passwordInput.addEventListener('input',()=>{
    const p=passwordInput.value;
    let score=0;
    if(p.length>=8) score++;
    if(/[A-Z]/.test(p)) score++;
    if(/[a-z]/.test(p)) score++;
    if(/\d/.test(p)) score++;
    if(/[^A-Za-z0-9]/.test(p)) score++;

    const widths=['0%','20%','40%','60%','80%','100%'];
    const colors=['#ef4444','#ef4444','#f97316','#f59e0b','#22c55e','#16a34a'];
    const labels=['Password strength','Very weak','Weak','Medium','Strong','Very strong'];

    strengthFill.style.width=widths[score];
    strengthFill.style.background=colors[score];
    strengthText.textContent=labels[score];
  });
}
