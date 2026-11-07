'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import type { Room } from 'livekit-client';

type Message={id:number;external_id:string;role:'user'|'assistant';body:string;created_at:string};
export type SupportIdentity={customerId:number;token:string;conversationId:string;previousMessages:Message[]};
type Action={id:string;title:string;details?:Record<string,unknown>};
type Notice={id:number;title:string;body:string;href:string;read:boolean};
type SupportState={identity:SupportIdentity|null;api:<T>(resource:string,method?:string,data?:unknown)=>Promise<T>;error:string;notices:Notice[]};
const Context=createContext<SupportState|null>(null);
export function useSupport(){const value=useContext(Context);if(!value)throw new Error('Support context missing');return value;}

export function SupportProvider({identity,room,children}:{identity:SupportIdentity|null;room:Room;children:ReactNode}) {
  const [action,setAction]=useState<Action|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [receipt,setReceipt]=useState<Record<string,unknown>|null>(null),[notices,setNotices]=useState<Notice[]>([]);
  const resolver=useRef<((value:string)=>void)|null>(null),dialog=useRef<HTMLDialogElement>(null);
  const api=useCallback(async<T,>(resource:string,method='GET',data?:unknown):Promise<T>=>{
    if(!identity)throw new Error('Open support from your signed-in MTS portal.');
    const response=await fetch('/api/support',{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${identity.token}`,'X-MTS-Customer':String(identity.customerId)},body:JSON.stringify({resource,method,data})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'Support request failed.');
    return result as T;
  },[identity]);
  useEffect(()=>{
    room.registerRpcMethod('mts.confirmAction',async(data)=>{
      if(!identity)throw new Error('A linked portal account is required');
      if(resolver.current)throw new Error('Another action is waiting for confirmation');
      const participant=room.remoteParticipants.get(data.callerIdentity);
      if(!participant?.isAgent)throw new Error('Only Sarah can request action review');
      const parsed=JSON.parse(data.payload) as Action;
      if(!parsed.id||!parsed.title)throw new Error('Invalid action review');
      setAction(parsed);setError('');
      return new Promise<string>(resolve=>{resolver.current=resolve;});
    });
    room.registerRpcMethod('mts.actionReceipt',async(data)=>{
      if(!room.remoteParticipants.get(data.callerIdentity)?.isAgent)throw new Error('Only Sarah can publish a receipt');
      setReceipt(JSON.parse(data.payload));return JSON.stringify({received:true});
    });
    return()=>{resolver.current?.(JSON.stringify({confirmed:false}));resolver.current=null;room.unregisterRpcMethod('mts.confirmAction');room.unregisterRpcMethod('mts.actionReceipt');};
  },[room,identity]);
  useEffect(()=>{if(action)dialog.current?.showModal();else dialog.current?.close();},[action]);
  useEffect(()=>{
    if(!identity)return;
    let alive=true;
    const refresh=()=>{void api<Notice[]>('notices').then(value=>{if(alive)setNotices(value);}).catch(()=>undefined);};
    refresh();const timer=window.setInterval(refresh,30000);window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
    return()=>{alive=false;clearInterval(timer);window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);};
  },[api,identity]);
  async function decide(confirmed:boolean){
    if(!action||busy)return;
    setBusy(true);
    try {await api(`confirmations/${action.id}/${confirmed?'accept':'cancel'}`,'POST');resolver.current?.(JSON.stringify({confirmed}));resolver.current=null;setAction(null);}
    catch(cause){setError(cause instanceof Error?cause.message:'Confirmation failed');}
    finally{setBusy(false);}
  }
  const paymentUrl=typeof receipt?.paymentUrl==='string'&&receipt.paymentUrl.startsWith('https://')?receipt.paymentUrl:'';
  return <Context.Provider value={{identity,api,error,notices}}>{children}
    <dialog ref={dialog} className="action-review" onCancel={event=>{event.preventDefault();void decide(false);}} aria-labelledby="review-title">
      <h2 id="review-title">Review with Sarah</h2><p>{action?.title}</p>
      {action?.details&&<dl>{Object.entries(action.details).filter(([key,value])=>!['confirmMonthly','items'].includes(key)&&value!==null&&value!==''&&value!==false).map(([key,value])=><div key={key}><dt>{key.replace(/([A-Z])/g,' $1').replace(/^./,c=>c.toUpperCase())}</dt><dd>{typeof value==='boolean'?'Yes':String(value)}</dd></div>)}</dl>}
      {error&&<p role="alert">{error}</p>}<div className="review-actions"><button disabled={busy} onClick={()=>void decide(false)}>Cancel</button><button disabled={busy} onClick={()=>void decide(true)}>{busy?'Saving…':'Confirm'}</button></div>
    </dialog>
    {receipt&&<aside className="action-receipt" role="status"><button aria-label="Dismiss receipt" onClick={()=>setReceipt(null)}>×</button><strong>Sarah’s update</strong><p>{String(receipt.number||receipt.ticketId&&`Ticket ${receipt.ticketId}`||receipt.id&&`Reference ${receipt.id}`||'Request processed')}</p><p>{String(receipt.status||receipt.syncStatus||'')}</p>{paymentUrl&&<a href={paymentUrl} target="_blank" rel="noopener noreferrer">Open secure payment</a>}</aside>}
  </Context.Provider>;
}

export function ConversationHistory(){
  const {identity,api}=useSupport();
  const [open,setOpen]=useState(false),[conversations,setConversations]=useState<{id:string;title:string;updated_at:string}[]>([]),[selected,setSelected]=useState(''),[messages,setMessages]=useState<Message[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[more,setMore]=useState(false);
  if(!identity)return null;
  async function load(){setOpen(value=>!value);if(open)return;setBusy(true);try{setConversations(await api('conversations'));setError('');}catch(cause){setError(String(cause));}finally{setBusy(false);}}
  async function view(id:string,before?:number){setBusy(true);try{const value=await api<Message[]>(`conversations/${id}/messages${before?`?before=${before}`:''}`);setMessages(previous=>before?[...value,...previous]:value);setSelected(id);setMore(value.length===100);setError('');}catch(cause){setError(String(cause));}finally{setBusy(false);}}
  return <section className="support-history"><button type="button" onClick={()=>void load()} aria-expanded={open}>Conversation history</button>{open&&<div className="history-panel">{busy&&<p role="status">Loading…</p>}{error&&<p role="alert">{error}</p>}{selected?<><button onClick={()=>setSelected('')}>Back to conversations</button>{more&&<button disabled={busy} onClick={()=>void view(selected,messages[0]?.id)}>Load earlier messages</button>}{messages.map(message=><article key={message.id}><small>{message.role==='user'?'You':'Sarah'} · {new Date(message.created_at).toLocaleString()}</small><p>{message.body}</p></article>)}</>:<>{conversations.map(conversation=><button key={conversation.id} onClick={()=>void view(conversation.id)}>{conversation.title}<small>{new Date(conversation.updated_at).toLocaleString()}</small></button>)}{conversations.length===0&&!busy&&<p>No earlier conversations.</p>}</>}</div>}</section>;
}

export function NotificationButton(){
  const {identity,api}=useSupport();const [status,setStatus]=useState(''),[busy,setBusy]=useState(false);
  if(!identity)return null;
  async function subscribe(){setBusy(true);try{
    if(!('serviceWorker'in navigator)||!('PushManager'in window))throw new Error('This browser does not support push notifications. In-app updates remain available.');
    const config=await api<{publicKey:string;configured:boolean}>('push');if(!config.configured)throw new Error('Device notifications are not configured yet. In-app updates are available.');
    const registration=await navigator.serviceWorker.register('/support-sw.js');await navigator.serviceWorker.ready;
    if(await Notification.requestPermission()!=='granted')throw new Error('Notifications were not enabled.');
    const bytes=Uint8Array.from(atob(config.publicKey.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
    const subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:bytes});
    await api('push','POST',{subscription:subscription.toJSON()});setStatus('Device notifications enabled.');
  }catch(cause){setStatus(cause instanceof Error?cause.message:'Could not enable notifications.');}finally{setBusy(false);}}
  return <div className="support-notifications"><button type="button" disabled={busy} onClick={()=>void subscribe()}>{busy?'Enabling…':'Enable device updates'}</button>{status&&<p role="status">{status}</p>}</div>;
}

export function NoticeBanner(){const {notices}=useSupport();const latest=notices.find(notice=>!notice.read);return latest?<aside className="notice-banner" role="status"><strong>{latest.title}</strong><p>{latest.body}</p></aside>:null;}
