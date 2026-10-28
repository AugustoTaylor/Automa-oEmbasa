// Simula a acao dos botoes do editor sem exigir Windows ou SAP.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'interface/index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'interface/style.css'), 'utf8');
const js = fs.readFileSync(path.join(root, 'interface/app.js'), 'utf8');
const modal = html.match(/<div id="saldoModeloModal"[\s\S]*?<div id="historyModal"/)?.[0];
assert.ok(modal, 'janela Confirmar modelo de contas precisa existir');
assert.doesNotMatch(modal, /saldoModeloSalvarTopoButton|Salvar altera(?:ç|&ccedil;)/i, 'editor deve ter um unico botao de salvar');
assert.match(modal, /id="saldoModeloSalvarButton"[\s\S]*?salvarModeloSaldoContasAtual\(\)/);
assert.equal((modal.match(/id="saldoModeloSalvarButton"/g) || []).length, 1, 'deve existir apenas Salvar e usar');
assert.match(css, /\.saldo-model-card\s*\{\s*display:\s*flex;/);
assert.match(css, /\.saldo-model-groups\s*\{[^}]*overflow-y:\s*auto/);
assert.match(css, /\.saldo-model-footer\s*\{[^}]*flex:\s*0 0 auto/);
const source = js.slice(js.indexOf('let salvamentoEditorSaldoContasEmAndamento'), js.indexOf('async function excluirModeloSaldoContasAtual'));
assert.ok(source.startsWith('let salvamentoEditor'), 'salvamento deve usar o rascunho completo');
const editable = {
  id: 'm-01', nome: 'Saldo de contas', aba: 'SET.26', grupos: [
    {id:'movimentos', nome:'BCO CTA MOVIMENTOS', ativo:false, contas:[
      {conta:'11311001', celula_conta:'A4', celula_valor:'B4', modo:'lote', ativo:true}
    ]},
    {id:'', nome:'NOVO GRUPO', ativo:true, contas:[
      {conta:'11370003', celula_conta:'C4', celula_valor:'D4', modo:'individual', ativo:true}
    ]}
  ]
};
async function run() {
  const button = () => ({disabled:false,textContent:''});
  const rodape=button();
  const error={textContent:'',classList:{hidden:true,add(){this.hidden=true;},remove(){this.hidden=false;}}};
  const calls=[], closed=[], statuses=[];
  let resolveServer;
  const state={saldoModeloRascunho:editable,saldoModeloId:'m-01',saldoPlanilhaAnalise:{caminho:'C:/modelo.xlsx',modelo:editable,grupos:[{id:'movimentos'}]},saldoModelos:[],saldoModelosCarregados:false,saldoPlanilhaGruposSelecionados:['movimentos']};
  const context={
    state,
    el:(id)=>({saldoModeloSalvarButton:rodape,saldoModeloErro:error,saldoGruposPainel:{classList:{contains:()=>false}},saldoModeloAba:{value:'SET.26'}})[id],
    window:{pywebview:{api:{salvar_modelo_saldo_contas:(data)=>{calls.push(data);return new Promise(resolve=>resolveServer=resolve);}}}},
    sincronizarCamposModeloSaldoContas:()=>editable,
    gruposSaldoContasSelecionados:()=>['movimentos'],
    persistirSelecaoGruposSaldoContas:()=>true,
    atualizarSeletorModelosSaldoContas:()=>{},
    renderizarAnaliseSaldoContas:()=>{},
    closeModal:(id)=>closed.push(id),
    setStatus:(...args)=>statuses.push(args),
    showSimpleOperationalToast:()=>{}
  };
  vm.createContext(context);
  vm.runInContext(source,context);
  const pending1=vm.runInContext('salvarModeloSaldoContasAtual()',context);
  const pending2=vm.runInContext('salvarModeloSaldoContasAtual()',context);
  assert.equal(calls.length,1, 'evitar salvamento duplicado');
  assert.equal(rodape.disabled,true);
  assert.equal(calls[0].modelo.grupos.length,2,'inclui novo grupo');
  assert.equal(calls[0].modelo.grupos[0].ativo,false,'preserva desativacao');
  assert.equal(calls[0].modelo.grupos[1].contas[0].modo,'individual','preserva modo individual');
  resolveServer({ok:true,modelo:{id:'m-01'},analise:{caminho:'C:/modelo.xlsx',modelo:editable,grupos:[{id:'movimentos'},{id:'novo_grupo'}]},modelos:[]});
  await Promise.all([pending1,pending2]);
  assert.equal(rodape.disabled,false);
  assert.deepEqual(closed,['saldoModeloModal']);
  assert.equal(state.saldoModeloRascunho,null);
  // Erro do backend: janela permanece aberta e explica o motivo.
  state.saldoModeloRascunho=editable;
  context.window.pywebview.api.salvar_modelo_saldo_contas=()=>Promise.resolve({ok:false,msg:'Conta nao corresponde a planilha'});
  await vm.runInContext('salvarModeloSaldoContasAtual()',context);
  assert.equal(error.classList.hidden,false);
  assert.equal(error.textContent,'Conta nao corresponde a planilha');
  assert.equal(closed.length,1);
  console.log('OK: botao unico Salvar e usar, payload de grupos/contas, trava duplicacao e erro.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
