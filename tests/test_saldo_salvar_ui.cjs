// Teste do fluxo de salvamento na interface, sem depender de SAP/Windows.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'interface/index.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'interface/app.js'), 'utf8');
assert.match(html, /id="saldoPlanilhaSalvarButton"/);
assert.match(html, /onclick="salvarConfiguracaoSaldoContas\(\)"/);

const funcs = js.slice(
  js.indexOf('function chaveSelecaoGruposSaldoContas('),
  js.indexOf('function atualizarSeletorModelosSaldoContas(')
);
const salvar = js.slice(
  js.indexOf('async function salvarConfiguracaoSaldoContas('),
  js.indexOf('async function salvarModeloSaldoContasAtual(')
);
assert.ok(funcs.startsWith('function chaveSelecao'));
assert.ok(salvar.startsWith('async function salvar'));

async function main() {
  const storage = new Map();
  const calls = [];
  const errors = [];
  const status = [];
  const selection = ['centralizadora'];
  const form = { saldoMes: {value: '10'}, saldoAno: {value: '2026'} };
  const analysis = {
    modelo: {id: 'modelo-7', nome: 'Modelo', aba: 'SET.26', grupos: []},
    modelo_id: 'modelo-7', caminho: 'C:/saldo.xlsx', grupos: [
      {id: 'centralizadora', quantidade: 3},
      {id: 'aplic_movimento', quantidade: 41}
    ]
  };
  const context = {
    SALDO_GRUPOS_STORAGE_PREFIX: 'embasa-saldo-grupos-v1',
    state: {
      usuarioAutenticado: {matricula: 'E397156'},
      saldoPlanilhaAnalise: analysis, saldoModeloId: 'modelo-7',
      saldoPlanilhaGruposSelecionados: [...selection],
      saldoPlanilhaBusy: false, saldoModelosCarregados: false
    },
    window: {pywebview: {api: {salvar_modelo_saldo_contas: async (data) => {
      calls.push(data);
      return {ok: true, modelo: {id: 'modelo-7'}, analise: analysis, modelos: []};
    }}}},
    localStorage: {
      getItem: (key) => storage.has(key) ? storage.get(key) : null,
      setItem: (key,value) => storage.set(key,value)
    },
    el: (name) => name === 'saldoGruposPainel' ? {classList:{contains:()=>false}} : form[name],
    gruposSaldoContasSelecionados: () => [...selection],
    limparErroSaldoContas: () => {},
    mostrarErroSaldoContas: (message) => errors.push(message),
    setSaldoPlanilhaBusy: (busy) => {context.state.saldoPlanilhaBusy=busy;},
    setStatus: (...args) => status.push(args),
    showSimpleOperationalToast: () => {},
    atualizarSeletorModelosSaldoContas: () => {},
    renderizarAnaliseSaldoContas: () => {form.saldoMes.value='09';form.saldoAno.value='2025';},
    atualizarPeriodoSaldoContas: () => {},
    log: () => {}
  };
  vm.createContext(context);
  vm.runInContext(funcs + '\n' + salvar, context);
  await vm.runInContext('salvarConfiguracaoSaldoContas()', context);
  assert.equal(calls.length,1, 'botao deve chamar a API real de salvamento');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])), {caminho:'C:/saldo.xlsx', modelo: analysis.modelo});
  assert.equal(errors.length,0);
  assert.deepEqual(Array.from(context.state.saldoPlanilhaGruposSelecionados), ['centralizadora']);
  assert.equal(context.state.saldoPlanilhaBusy,false);
  assert.equal(form.saldoMes.value,'10', 'Salvar nao pode alterar o mes da consulta');
  assert.equal(form.saldoAno.value,'2026');
  const saved=JSON.parse(storage.get('embasa-saldo-grupos-v1:e397156:modelo-7'));
  assert.deepEqual(saved,['centralizadora'], 'deve salvar os grupos marcados');
  assert.deepEqual(Array.from(vm.runInContext('selecionarGruposSalvosOuTodosSaldoContas(state.saldoPlanilhaAnalise)',context)),['centralizadora']);
  context.state.usuarioAutenticado={matricula: 'OUTRO'};
  assert.deepEqual(Array.from(vm.runInContext('selecionarGruposSalvosOuTodosSaldoContas(state.saldoPlanilhaAnalise)',context)),['centralizadora','aplic_movimento']);
  // Sem grupos marcados (opcao Nenhum) deve continuar vazio, nao resetar automaticamente.
  context.state.usuarioAutenticado={matricula: 'E397156'};
  selection.length=0;
  await vm.runInContext('salvarConfiguracaoSaldoContas()', context);
  assert.deepEqual(JSON.parse(storage.get('embasa-saldo-grupos-v1:e397156:modelo-7')),[]);
  assert.equal(calls.length,2);
  // Erro no backend: nao afirmar salvamento nem mudar preferencias locais.
  context.window.pywebview.api.salvar_modelo_saldo_contas = async () => ({ok:false, msg:'Modelo divergente'});
  selection.push('aplic_movimento');
  await vm.runInContext('salvarConfiguracaoSaldoContas()', context);
  assert.equal(errors.at(-1),'Modelo divergente');
  assert.deepEqual(JSON.parse(storage.get('embasa-saldo-grupos-v1:e397156:modelo-7')),[]);
  assert.equal(context.state.saldoPlanilhaBusy,false);
  console.log('OK: salvamento, preferencias por usuario, periodo preservado e falhas validadas.');
}
main().catch((e)=>{console.error(e);process.exitCode=1;});
