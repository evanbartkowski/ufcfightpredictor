'use strict';

// An architectural octagon study, separate from forecast data and interactions.
(() => {
  const canvas = document.querySelector('[data-arena-canvas]');
  if (!canvas) return;
  const ctx = canvas.getContext('2d', {alpha:true});
  if (!ctx) return;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  let width = 0, height = 0, unit = 0, frame = 0, raf = 0, last = -Infinity;

  function size() {
    width = innerWidth; height = innerHeight;
    const dpr = Math.min(devicePixelRatio || 1, width < 700 ? 1 : 1.5);
    canvas.width = Math.round(width*dpr); canvas.height = Math.round(height*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    unit = Math.min(325,Math.max(145,width*.205));
    draw(0);
  }

  function draw(time) {
    ctx.clearRect(0,0,width,height);
    const phase = time/1000;
    const angle = -.26 + Math.sin(phase/21)*.17;
    const pitch = .48;
    const cosAngle=Math.cos(angle),sinAngle=Math.sin(angle),cosPitch=Math.cos(pitch),sinPitch=Math.sin(pitch);
    const origin = {x:width*(width < 700 ? .78 : .69),y:height*(width < 700 ? .30 : .47)};
    const point = (x,y,z) => {
      const rx = x*cosAngle-z*sinAngle;
      const rz = x*sinAngle+z*cosAngle;
      const py = y*cosPitch-rz*sinPitch;
      const pz = y*sinPitch+rz*cosPitch;
      const scale = unit*4.5/(4.5+pz);
      return {x:origin.x+rx*scale,y:origin.y-py*scale};
    };
    const vertex = (i, y=0, radius=1) => [Math.cos(i*Math.PI/4+Math.PI/8)*radius,y,Math.sin(i*Math.PI/4+Math.PI/8)*radius];
    const move = v => { const p=point(...v);ctx.moveTo(p.x,p.y); };
    const line = v => { const p=point(...v);ctx.lineTo(p.x,p.y); };
    const ring = (y,radius,color,lineWidth=1) => {
      ctx.beginPath();move(vertex(0,y,radius));
      for(let i=1;i<=8;i++)line(vertex(i,y,radius));
      ctx.strokeStyle=color;ctx.lineWidth=lineWidth;ctx.stroke();
    };
    ring(-.06,1.27,'rgba(62,105,125,.25)');
    ring(-.06,1.37,'rgba(62,105,125,.10)');
    ring(0,1.06,'rgba(74,124,145,.28)');
    ring(.78,1,'rgba(64,154,175,.33)',1.2);
    ring(.81,1,'rgba(54,113,140,.16)');

    // The cage weave follows each 3D face rather than a screen-space texture.
    ctx.beginPath();
    const steps=width<700?5:9;
    for(let side=0;side<8;side++) {
      const a=vertex(side),b=vertex(side+1);
      const face=(u,v)=>[a[0]+(b[0]-a[0])*u,v*.78,a[2]+(b[2]-a[2])*u];
      for(let row=0;row<6;row++)for(let col=0;col<steps;col++) {
        move(face(col/steps,row/6));line(face((col+1)/steps,(row+1)/6));
        move(face((col+1)/steps,row/6));line(face(col/steps,(row+1)/6));
      }
    }
    ctx.lineWidth=.55;ctx.strokeStyle='rgba(51,101,123,.13)';ctx.stroke();
    ctx.beginPath();
    for(let i=0;i<8;i++) { move(vertex(i));line(vertex(i,.82)); }
    ctx.lineWidth=1.5;ctx.strokeStyle='rgba(62,127,151,.31)';ctx.stroke();
    ctx.beginPath();
    for(let i=0;i<8;i++) { move(vertex(i,0,1.07));line(vertex(i,0,1.24)); }
    ctx.strokeStyle='rgba(67,122,141,.28)';ctx.lineWidth=1;ctx.stroke();

    // Corner-colored arcs suggest movement and opposing striking ranges.
    for(const [sign,color] of [[-1,'255,52,98'],[1,'42,221,238']]) {
      const start=point(sign*.98,.24,-.4),end=point(-sign*.4,.42,.56);
      const control={x:origin.x+sign*unit*.18,y:origin.y-unit*.95};
      const alpha=.20+Math.sin(phase*.45+sign)*.035;
      ctx.beginPath();ctx.moveTo(start.x,start.y);ctx.quadraticCurveTo(control.x,control.y,end.x,end.y);
      ctx.strokeStyle=`rgba(${color},${alpha})`;ctx.lineWidth=1;ctx.stroke();
      const t=((phase*.035+(sign+1)*.23)%1);
      const x=(1-t)*(1-t)*start.x+2*(1-t)*t*control.x+t*t*end.x;
      const y=(1-t)*(1-t)*start.y+2*(1-t)*t*control.y+t*t*end.y;
      ctx.shadowColor=`rgb(${color})`;ctx.shadowBlur=9;
      ctx.fillStyle=`rgba(${color},.7)`;ctx.beginPath();ctx.arc(x,y,1.7,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;
    }
    const center=point(0,0,0);
    ctx.strokeStyle='rgba(65,116,134,.35)';ctx.lineWidth=.8;
    ctx.beginPath();ctx.moveTo(center.x-9,center.y);ctx.lineTo(center.x+9,center.y);ctx.moveTo(center.x,center.y-5);ctx.lineTo(center.x,center.y+5);ctx.stroke();
    canvas.dataset.frames=String(++frame);
  }

  function tick(time) {
    if (time-last>=1000/24) {draw(time);last=time;}
    raf=requestAnimationFrame(tick);
  }
  function playback() {
    cancelAnimationFrame(raf);raf=0;
    canvas.dataset.motion=motion.matches?'static':'animated';
    if (document.hidden || motion.matches) {draw(0);return;}
    last=-Infinity;raf=requestAnimationFrame(tick);
  }
  addEventListener('resize',size,{passive:true});
  document.addEventListener('visibilitychange',playback);
  motion.addEventListener('change',playback);
  size();playback();
})();
