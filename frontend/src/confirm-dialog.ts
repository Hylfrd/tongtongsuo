import './confirm-dialog.css';

type ConfirmOptions = {title:string; message:string; confirmLabel?:string; cancelLabel?:string; danger?:boolean};
let active=false;
let sequence=0;

/** One confirmation at a time; a duplicate request never approves a second action. */
export async function confirmDialog(options:ConfirmOptions):Promise<boolean> {
  if(active)return false;
  active=true;
  return new Promise<boolean>(resolve=>{
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const dialog=document.createElement('dialog');
    const lifetime=new AbortController();
    let done=false, closing=false, answer=false;
    let timer:ReturnType<typeof setTimeout>|undefined;
    const id=`admin-confirm-${++sequence}`;
    dialog.className='adm-dialog adm-confirm-dialog';
    dialog.setAttribute('aria-labelledby',`${id}-title`);
    dialog.setAttribute('aria-describedby',`${id}-message`);
    const heading=document.createElement('h2');
    heading.id=`${id}-title`;
    heading.textContent=options.title;
    const description=document.createElement('p');
    description.id=`${id}-message`;
    description.textContent=options.message;
    const actions=document.createElement('div');
    actions.className='adm-confirm-actions';
    const cancel=document.createElement('button');
    cancel.type='button';
    cancel.className='adm-outline';
    cancel.textContent=options.cancelLabel??'取消';
    cancel.autofocus=true;
    const confirm=document.createElement('button');
    confirm.type='button';
    confirm.className=`adm-button${options.danger?' adm-confirm-danger':''}`;
    confirm.textContent=options.confirmLabel??'确定';
    actions.append(cancel,confirm);
    dialog.append(heading,description,actions);

    function finish(value:boolean,restoreFocus=true) {
      if(done)return;
      done=true;
      if(timer)clearTimeout(timer);
      lifetime.abort();
      if(dialog.open)dialog.close();
      dialog.remove();
      active=false;
      if(restoreFocus&&previous?.isConnected)previous.focus({preventScroll:true});
      resolve(value);
    }
    function close(value:boolean) {
      if(done||closing)return;
      closing=true;
      answer=value;
      cancel.disabled=true;
      confirm.disabled=true;
      dialog.classList.add('is-closing');
      const duration=getComputedStyle(dialog).animationDuration.split(',')[0].trim();
      const milliseconds=Number.parseFloat(duration)*(duration.endsWith('ms')?1:1000);
      timer=setTimeout(()=>finish(answer),Number.isFinite(milliseconds)&&milliseconds>0?milliseconds+60:0);
    }
    cancel.addEventListener('click',()=>close(false),{signal:lifetime.signal});
    confirm.addEventListener('click',()=>close(true),{signal:lifetime.signal});
    dialog.addEventListener('cancel',event=>{event.preventDefault();close(false);},{signal:lifetime.signal});
    dialog.addEventListener('close',()=>finish(false),{signal:lifetime.signal});
    dialog.addEventListener('animationend',event=>{if(closing&&event.target===dialog)finish(answer);},{signal:lifetime.signal});
    window.addEventListener('pagehide',()=>finish(false,false),{signal:lifetime.signal});
    window.addEventListener('popstate',()=>finish(false,false),{signal:lifetime.signal});
    document.body.append(dialog);
    try {
      dialog.showModal();
      cancel.focus({preventScroll:true});
    } catch { finish(false); }
  });
}
