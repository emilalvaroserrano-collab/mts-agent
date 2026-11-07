self.addEventListener('push',event=>{
  let data={};try{data=event.data?.json()||{};}catch{return;}
  event.waitUntil(self.registration.showNotification(data.title||'Manage Tech Services',{body:data.body||'Your account has an update.',tag:data.tag||'mts-update',data:{href:data.href||'/portal/notifications'}}));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  // Push messages never control the destination origin.
  const base='https://mts.192-888-702-801.site';
  const path=String(event.notification.data?.href||'/portal/notifications');
  const destination=new URL(path.startsWith('/portal/')&&!path.startsWith('//')?path:'/portal/notifications',base).href;
  event.waitUntil(self.clients.openWindow(destination));
});
