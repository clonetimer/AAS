/* Shared data-resource picker for browsing, training and evaluation.
 * Search filters the AVAILABLE table list; it never silently drops selected tables.
 * The selection order is the feature order. Nothing is submitted until Apply.
 */
(() => {
  'use strict';
  const fields = [['model', '型号'], ['subsystem', '分系统'], ['device', '单机'], ['variable', '参量']];
  const styles = document.createElement('style');
  styles.textContent = `
  .aas-resource-trigger{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:9px 15px;border:1px solid #248eac;border-radius:9px;background:#e7f5f8;color:#126780;font-weight:700;cursor:pointer}
  .aas-resource-trigger:hover{background:#d4eef6}
  .aas-resource-dialog{border:0;border-radius:14px;width:min(900px,calc(100vw - 28px));max-height:min(82vh,850px);padding:0;box-shadow:0 20px 85px #001e3288;color:#273747;background:#fff}
  .aas-resource-dialog::backdrop{background:#0c1a2999}
  .aas-picker-head,.aas-picker-footer{padding:14px 18px;display:flex;align-items:center;justify-content:space-between;gap:12px}
  .aas-picker-head{border-bottom:1px solid #e1eaf0}.aas-picker-footer{border-top:1px solid #e1eaf0}
  .aas-picker-head h2{font-size:18px;margin:0}.aas-picker-head p{margin:3px 0 0;font-size:12px;color:#66788b}
  .aas-picker-close{font-size:23px;background:transparent;border:0;cursor:pointer;line-height:1}
  .aas-picker-search{width:100%;box-sizing:border-box;border:1px solid #cfdde6;border-radius:8px;padding:10px;font:inherit}
  .aas-picker-filters{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}
  .aas-picker-filters label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:#617184}
  .aas-picker-filters select{min-width:0;max-width:100%;padding:7px;border:1px solid #d7e2eb;border-radius:7px;background:#fff}
  .aas-picker-body{padding:14px 18px;overflow-y:auto;max-height:calc(82vh - 146px)}
  .aas-picker-columns{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(230px,1fr);gap:12px;margin-top:12px}
  .aas-picker-panel{border:1px solid #e1eaf0;border-radius:9px;min-height:180px;overflow:hidden}
  .aas-picker-panel h3{font-size:13px;padding:10px 12px;background:#f5f9fb;margin:0;display:flex;justify-content:space-between;align-items:center}
  .aas-picker-list{max-height:260px;overflow-y:auto;padding:5px 9px}
  .aas-picker-entry{display:flex;gap:8px;align-items:center;padding:7px 3px;border-bottom:1px solid #edf3f6;font-size:13px;min-width:0}
  .aas-picker-entry:last-child{border:0}.aas-picker-entry span{flex:1;min-width:0;overflow-wrap:anywhere}
  .aas-picker-entry small{font-size:11px;color:#748494;display:block;margin-top:2px}
  .aas-picker-entry button,.aas-picker-mini{border:1px solid #d2e0e9;border-radius:5px;background:#fff;cursor:pointer;padding:3px 6px}
  .aas-picker-footer button{border-radius:7px;padding:9px 16px;cursor:pointer;border:1px solid #b9ccd7;background:#fff}
  .aas-picker-footer .aas-picker-apply{background:#167e9e;color:#fff;border:1px solid #167e9e}
  .aas-picker-empty{padding:15px;color:#7c8b9a;font-size:13px}
  @media(max-width:700px){.aas-picker-filters{grid-template-columns:repeat(2,minmax(0,1fr))}.aas-picker-columns{grid-template-columns:1fr}.aas-picker-list{max-height:155px}}
  `;
  document.head.appendChild(styles);
  function node(tag, cls, label) { const el = document.createElement(tag); if(cls) el.className=cls; if(label!==undefined) el.textContent=label; return el; }
  const distinct = (items, prop) => [...new Set(items.flatMap(x => (x.hierarchy || []).map(h => h[prop]).filter(Boolean)))].sort();
  function mount(config) {
    const rows = Array.isArray(config.items) ? config.items : [];
    const byName = new Map(rows.map(item => [item.name, item]));
    const max = config.max || 16;
    let chosen = [...new Set(config.selected || [])].filter(name => byName.has(name)).slice(0,max);
    const filters = Object.fromEntries(fields.map(([k]) => [k, '']));
    let query = '';
    let notice = '';
    const dialog = node('dialog','aas-resource-dialog');
    const head=node('div','aas-picker-head'),titlebox=node('div');
    titlebox.append(node('h2','',config.title || '选择数据表'),node('p','','搜索、筛选并按顺序选择，最后统一确认'));
    const close=node('button','aas-picker-close','×');close.type='button';close.setAttribute('aria-label','关闭选择器');close.onclick=()=>dialog.close();head.append(titlebox,close);
    const body=node('div','aas-picker-body');
    const search=node('input','aas-picker-search');search.type='search';search.placeholder='搜索表名、型号、分系统、单机、参量、数据用途';search.setAttribute('aria-label','检索数据资源');
    const filterWrap=node('div','aas-picker-filters');
    fields.forEach(([key,label])=>{
      const field=node('label','',label), select=node('select');
      select.append(new Option('全部', ''));
      distinct(rows,key).forEach(value=>select.append(new Option(value,value)));
      select.onchange=()=>{ filters[key]=select.value; render(); };
      field.append(select); filterWrap.append(field);
    });
    const cols=node('div','aas-picker-columns');
    const available=node('div','aas-picker-panel'),selected=node('div','aas-picker-panel');
    const availableHead=node('h3'), availableTitle=node('span','','可用数据表'),all=node('button','aas-picker-mini','勾选筛选结果');
    all.type='button';availableHead.append(availableTitle,all);
    const availableList=node('div','aas-picker-list');available.append(availableHead,availableList);
    const selectedHead=node('h3'),selectedTitle=node('span','','已选数据表（顺序即特征顺序）'),clear=node('button','aas-picker-mini','清空');
    clear.type='button';selectedHead.append(selectedTitle,clear);
    const selectedList=node('div','aas-picker-list');selected.append(selectedHead,selectedList);
    cols.append(available,selected);body.append(search,filterWrap,cols);
    const footer=node('div','aas-picker-footer'),summary=node('span','',''),actions=node('div');
    const cancel=node('button','','取消'),apply=node('button','aas-picker-apply','确认选择');
    cancel.type=apply.type='button';cancel.onclick=()=>dialog.close();
    apply.onclick=()=>{ if(config.min && chosen.length<config.min) { notice=`至少选择 ${config.min} 张数据表`;render();return; } const selection=chosen.slice();dialog.close();Promise.resolve(config.onApply?.(selection)).catch(err=>window.alert(err.message)); };
    actions.append(cancel,apply);footer.append(summary,actions);dialog.append(head,body,footer);
    document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    function matching(item){
      const haystack=[item.name,item.data_role,item.role_label,...(item.hierarchy||[]).flatMap(h=>fields.map(([key])=>h[key]))].join(' ').toLocaleLowerCase();
      return haystack.includes(query) && ((!(item.hierarchy||[]).length && fields.every(([key])=>!filters[key])) || (item.hierarchy||[]).some(h=>fields.every(([key])=>!filters[key] || h[key]===filters[key])));
    }
    function render(){
      const visible=rows.filter(matching);
      availableList.replaceChildren();selectedList.replaceChildren();
      availableTitle.textContent=`可用数据表 · ${visible.length} 张`;
      selectedTitle.textContent=`已选 ${chosen.length}/${max} 张`;
      for (const item of visible){
        const row=node('label','aas-picker-entry'),checkbox=node('input');checkbox.type='checkbox';checkbox.checked=chosen.includes(item.name);
        const path=(item.hierarchy||[]).map(h=>fields.map(([key])=>h[key]).filter(Boolean).join(' / '))[0] || '';
        const count=Number.isFinite(Number(item.row_count)) ? `${item.row_count} 条` : '';
        const range=item.time_start!=null && item.time_end!=null ? `${item.time_start} → ${item.time_end}` : '';
        const info=node('span'),text=node('strong','',item.name),meta=node('small','', [path,item.role_label||item.data_role,count,range].filter(Boolean).join(' · '));
        if(range) meta.title=`时间覆盖：${range}`;
        info.append(text,meta);row.append(checkbox,info);
        checkbox.onchange=()=>{if(checkbox.checked){if(chosen.length>=max){if(max===1){chosen=[item.name];notice='';render();return;}notice=`最多选择 ${max} 张数据表`;checkbox.checked=false;render();return;}chosen.push(item.name);} else chosen=chosen.filter(n=>n!==item.name);notice='';render();};
        availableList.append(row);
      }
      if(!visible.length)availableList.append(node('p','aas-picker-empty','未找到匹配的数据表'));
      chosen.forEach((name,index)=>{
        const row=node('div','aas-picker-entry'),text=node('span','',`${index+1}. ${name}`);
        const up=node('button','', '↑'),down=node('button','', '↓'),remove=node('button','', '×');
        [up,down,remove].forEach(b=>b.type='button');up.disabled=index===0;down.disabled=index===chosen.length-1;
        up.onclick=()=>{[chosen[index-1],chosen[index]]=[chosen[index],chosen[index-1]];render();};
        down.onclick=()=>{[chosen[index+1],chosen[index]]=[chosen[index],chosen[index+1]];render();};
        remove.onclick=()=>{chosen=chosen.filter(n=>n!==name);render();};row.append(text,up,down,remove);selectedList.append(row);
      });
      if(!chosen.length)selectedList.append(node('p','aas-picker-empty','尚未选择数据表'));
      const purpose=config.purpose || 'train';
      const usage=purpose==='browse' ? '首张作为表格明细主表' :
        (purpose==='evaluate' ? '顺序对应模型输入特征' : '首张作为同步时间轴');
      summary.textContent=notice || `已选 ${chosen.length} 张 · ${usage}`;
      apply.disabled=!!config.min && chosen.length<config.min;
    }
    search.oninput=()=>{query=search.value.trim().toLocaleLowerCase();render();};
    clear.onclick=()=>{chosen=[];render();};
    all.onclick=()=>{for(const item of rows.filter(matching)) {if(chosen.length>=max)break;if(!chosen.includes(item.name))chosen.push(item.name);}render();};
    render();dialog.showModal();search.focus();
  }
  window.AASResourcePicker={open:mount};
})();
