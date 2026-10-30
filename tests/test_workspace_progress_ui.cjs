// Garante que trocar de seção não apaga o andamento de uma operação SAP.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'interface/app.js'), 'utf8');
const start = js.indexOf('function obterMonitorOperacaoAtiva()');
const end = js.indexOf('function mostrarPainelWorkspace(', start);
assert.ok(start > 0 && end > start, 'controle de estado dos monitores ausente');

const stages = {
  boletos: [{id: 'XD03'}],
  saldos: [{id: 'PLANILHA'}, {id: 'SAP_SALDOS'}]
};
const context = {
  MONITOR_STAGES: stages,
  state: {
    saldoPlanilhaBusy: true,
    extratoRunning: false,
    flowRunning: false,
    monitorMode: 'saldos',
    monitorSnapshots: {},
    monitorStatusSnapshots: {
      saldos: {texto: 'Consultando saldos no SAP', classe: 'running'}
    },
    etapaAtual: 1,
    etapasStatus: ['done', 'active'],
    etapasProgresso: [100, 47],
    etapasMensagens: ['Modelo validado', 'Consultando conta 20/41'],
    etapasConstruidas: true,
    statusSnapshot: 'running::Consultando saldos no SAP'
  },
  ETAPAS: stages.saldos,
  DISPLAY_STAGE_MAP: new Map(),
  ETAPA_NODE_CACHE: [{}, {}],
  criarDisplayStageMap: () => new Map(),
  construirEtapas: () => {
    context.ETAPA_NODE_CACHE.length = context.ETAPAS.length;
  },
  resetarEtapas: () => {
    context.state.etapaAtual = null;
    context.state.etapasStatus = context.ETAPAS.map(() => 'pending');
    context.state.etapasProgresso = context.ETAPAS.map(() => 0);
    context.state.etapasMensagens = context.ETAPAS.map(() => '');
  },
  setEtapa: (index, status, progress, message) => {
    context.state.etapasStatus[index] = status;
    context.state.etapasProgresso[index] = progress;
    context.state.etapasMensagens[index] = message;
  },
  setStatus: (texto, classe) => {
    context.state.statusSnapshot = `${classe}::${texto}`;
    context.state.monitorStatusSnapshots[context.state.monitorMode] = {texto, classe};
  }
};

vm.createContext(context);
vm.runInContext(js.slice(start, end), context);

// Enquanto a consulta está em andamento, a navegação não troca o monitor.
const bloqueado = vm.runInContext('setMonitorMode("boletos")', context);
assert.equal(bloqueado, false);
assert.equal(context.state.monitorMode, 'saldos');
assert.equal(context.state.etapasProgresso[1], 47);
assert.equal(context.state.etapasMensagens[1], 'Consultando conta 20/41');

// Depois de concluída, cada seção pode ser aberta sem perder sua fotografia.
context.state.saldoPlanilhaBusy = false;
const mudou = vm.runInContext('setMonitorMode("boletos")', context);
assert.equal(mudou, true);
assert.equal(context.state.monitorMode, 'boletos');
assert.equal(context.state.monitorSnapshots.saldos.progresso[1], 47);

context.state.etapaAtual = 0;
context.state.etapasStatus = ['done'];
context.state.etapasProgresso = [100];
context.state.etapasMensagens = ['Boleto concluído'];

vm.runInContext('setMonitorMode("saldos")', context);
assert.equal(context.state.monitorMode, 'saldos');
assert.equal(context.state.etapaAtual, 1);
assert.equal(context.state.etapasStatus[1], 'active');
assert.equal(context.state.etapasProgresso[1], 47);
assert.equal(context.state.etapasMensagens[1], 'Consultando conta 20/41');
assert.equal(
  context.state.statusSnapshot,
  'running::Consultando saldos no SAP'
);

console.log('OK: troca de seção preserva progresso e status por operação.');
