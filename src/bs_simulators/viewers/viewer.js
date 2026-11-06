/** Observe committed snapshots; rendering never advances the simulation. */
import {scenes, fmt} from './scenes.js';
const el=id=>document.getElementById(id);
const scene=scenes[document.documentElement.dataset.viewer];
let state=null;
let signals=null;
el('scene').style.aspectRatio=scene.aspect;
function draw(){
 const ranges=scene.draw(el('scene'),state,signals||{outputs:[],inputs:[]});
 el('scales').textContent=ranges?.map(({label,range})=>label+': ['+range.join(', ')+']').join('; ')||'—';
}
/** Load ordered signal descriptions once, or again after the observed run restarts. */
async function loadSignals(){
 const response=await fetch('/api/signals',{cache:'no-store',signal:AbortSignal.timeout(3000)});
 if(!response.ok)throw new Error('Signal metadata: HTTP '+response.status);
 const descriptions=await response.json();
 if(!Array.isArray(descriptions))throw new Error('Invalid signal metadata');
 signals={outputs:descriptions.filter(s=>s.direction==='output').slice(0,scene.states),
          inputs:descriptions.filter(s=>s.direction==='input')};
}
async function poll(){
 try{
  const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(3000)});
  if(!response.ok)throw new Error('HTTP '+response.status);
  const next=await response.json();
  if(!Array.isArray(next.x)||next.x.length!==scene.states||!next.x.every(Number.isFinite)||
     !Array.isArray(next.u)||!next.u.every(Number.isFinite))throw new Error('Invalid simulation state');
  const restarted=state&&(next.seq<state.seq||next.t<state.t);
  if(!signals||restarted)await loadSignals();
  if(restarted)scene.reset?.();
  state=next;scene.observe?.(state);
  el('time').textContent='t = '+fmt(state.t,1)+' s';
  const statuses={ready:'Ready',running:'Live',finished:'Experiment finished',stopped:'Simulation stopped',error:'Simulation error'};
  el('status').textContent=statuses[state.status]||state.status;
  el('motion').textContent=state.x.map(v=>fmt(v,6)).join(', ');
  el('input').textContent=state.u.map(v=>fmt(v,4)).join(', ');
  el('parameters').textContent=Object.entries(state.parameters||{}).map(([k,v])=>k+' = '+v).join('; ');
  el('controller').textContent=state.active_controller??'—';
  el('timing').textContent=fmt(state.wall_elapsed)+' s / '+fmt(1000*state.lag,1)+' ms';
  el('sequence').textContent=state.input_seq+' / '+(state.pending_input_seq??'—');
  el('error').textContent=state.error||'';draw();
 }catch(error){
  el('status').textContent=state?'Disconnected · retaining the last image':'Cannot connect to the simulator';
  el('error').textContent=error.message;
 }
 setTimeout(poll,50);
}
window.addEventListener('resize',draw);draw();poll();
