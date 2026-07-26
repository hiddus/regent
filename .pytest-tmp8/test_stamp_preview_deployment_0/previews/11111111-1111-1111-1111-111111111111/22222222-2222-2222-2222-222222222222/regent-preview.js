
(function(){
  var btn=document.querySelector('[data-regent-event]');
  if(!btn){return;}
  btn.addEventListener('click', function(){
    var meta=document.querySelector('meta[name="regent-deployment-id"]');
    var q=new URLSearchParams(location.search).get('deployment_id');
    var id=(meta && meta.content) || q || '';
    if(!id){
      document.documentElement.setAttribute('data-regent-obs','missing-id');
      return;
    }
    fetch('/v1/deployments/'+id+'/events',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({event_id:'click-'+Date.now(),event_name:'activation'})
    }).then(function(r){
      document.documentElement.setAttribute('data-regent-obs', r.ok ? 'ok' : 'err');
    }).catch(function(){
      document.documentElement.setAttribute('data-regent-obs','err');
    });
  });
})();
