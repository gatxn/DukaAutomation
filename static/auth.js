document.querySelectorAll('input[type="password"]').forEach(input=>{
  const wrapper=document.createElement('div');wrapper.className='password-control';
  input.parentNode.insertBefore(wrapper,input);wrapper.append(input);
  const button=document.createElement('button');button.type='button';button.textContent='Show';
  button.setAttribute('aria-label',`Show ${input.name==='password2'?'password confirmation':'password'}`);
  button.setAttribute('aria-pressed','false');
  button.addEventListener('click',()=>{const show=input.type==='password';input.type=show?'text':'password';button.textContent=show?'Hide':'Show';button.setAttribute('aria-pressed',String(show));button.setAttribute('aria-label',`${show?'Hide':'Show'} ${input.name==='password2'?'password confirmation':'password'}`)});
  wrapper.append(button);
});
document.querySelectorAll('.auth-card form').forEach(form=>form.addEventListener('submit',()=>{const button=form.querySelector('button.primary');if(form.checkValidity()){button.disabled=true;button.textContent='Opening your shop…'}}));
