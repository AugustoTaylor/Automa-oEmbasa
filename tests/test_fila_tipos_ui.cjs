// Garante que cada item da fila mantenha seu proprio tipo e valor.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const js = fs.readFileSync(path.join(root, 'interface/app.js'), 'utf8');

function trecho(inicio, fim) {
  const start = js.indexOf(inicio);
  const end = js.indexOf(fim, start);
  assert.ok(start >= 0, `inicio ausente: ${inicio}`);
  assert.ok(end > start, `fim ausente: ${fim}`);
  return js.slice(start, end);
}

const source = [
  trecho('function cloneEndereco(', 'function formatarEnderecoResumo('),
  trecho('function getTipoSolicitacaoLabel(', 'function coletarEnderecosParaExecucao('),
  trecho('function adicionarEmpreendimentoNaFila(', 'function removerEmpreendimentoDaFila('),
  trecho('function normalizarChaveFila(', 'function resolverInicioRetomadaFilaClientes('),
  trecho('function montarPayloadsLoteAtual(', '// Bloqueia botões enquanto o fluxo SAP está executando.'),
  trecho('function montarCheckpointClienteParaProximo(', 'async function executarLoteEmpreendimentos('),
  trecho('function montarPayloadsClienteFila(', 'function obterProximoItemFilaClientes('),
  trecho('async function executarFilaClientes(', 'function limparFormularioMultaContratual(')
].join('\n');

const form = {
  tipo: {value: 'esgoto'},
  valor: {value: '9.999,99'}
};
const itensLote = [
  {empreendimento: 'Abacate', rua: 'Rua A', numero: '1', cep: '40000-001', bairro: 'A', cidade: 'Salvador', estado: 'BA', tipo: 'viabilidade', valor: '1.075,08', modo_valor: 'manual'},
  {empreendimento: 'Limao', rua: 'Rua B', numero: '2', cep: '40000-002', bairro: 'B', cidade: 'Salvador', estado: 'BA', tipo: 'agua', valor: '2.441,87', modo_valor: 'auto'}
];

const context = {
  TIPOS_SOLICITACAO: {
    '': 'Selecione',
    viabilidade: 'Viabilidade',
    agua: 'Projeto Água',
    esgoto: 'Projeto Esgoto'
  },
  state: {valueMode: 'auto', clienteQueueSeq: 0, batchRunning: false, empreendimentosFila: []},
  window: {resetarProgresso: () => {}},
  el: (id) => form[id],
  formatarCepValue: (value) => String(value || ''),
  sanitizarCidade: (value) => String(value || ''),
  limparDocumento: (value) => String(value || '').replace(/\D/g, ''),
  formatarDocumentoResumo: (value) => String(value || ''),
  validarBaseAntesDoFluxo: () => ({docInfo: {digitos: '11222333000144'}, tipo: 'esgoto'}),
  coletarEnderecosParaExecucao: () => itensLote,
  limparMensagensValidacao: () => {},
  validarFormularioAntesDoFluxo: () => ({
    tipo: form.tipo.value,
    endereco: context.enderecoAtual
  }),
  renderizarFilaEmpreendimentos: () => {},
  limparCamposEndereco: () => {},
  setEmpreendimentoAberto: () => {},
  formatarEnderecoResumo: (endereco) => endereco.empreendimento || '',
  setFlowButtonsBusy: () => {},
  hideResumeBox: () => {},
  log: () => {},
  setStatus: () => {},
  processarEtapaTempoReal: () => {},
  removerPayloadConcluidoDaFilaClientes: () => {},
  limparFilaClientes: () => {},
  limparFilaEmpreendimentos: () => {},
  limparFormularioCriacaoBoleto: () => {},
  obterProximoItemFilaClientes: (planos, clienteIndex, payloadIndex) => {
    if (payloadIndex + 1 < planos[clienteIndex].payloads.length) {
      return {
        cliente: planos[clienteIndex].cliente,
        payload: planos[clienteIndex].payloads[payloadIndex + 1]
      };
    }
    if (clienteIndex + 1 < planos.length) {
      return {
        cliente: planos[clienteIndex + 1].cliente,
        payload: planos[clienteIndex + 1].payloads[0]
      };
    }
    return null;
  },
  aguardarConfirmacaoFilaClientes: async () => true
};

vm.createContext(context);
vm.runInContext(source, context);

context.enderecoAtual = itensLote[0];
form.tipo.value = 'viabilidade';
form.valor.value = '1.075,08';
vm.runInContext('adicionarEmpreendimentoNaFila()', context);
context.enderecoAtual = itensLote[1];
form.tipo.value = 'agua';
form.valor.value = '2.441,87';
vm.runInContext('adicionarEmpreendimentoNaFila()', context);
assert.deepEqual(
  Array.from(context.state.empreendimentosFila, (item) => item.tipo),
  ['viabilidade', 'agua'],
  'o botao adicionar deve fotografar o tipo atual de cada empreendimento'
);

const clienteMisto = {
  doc: '11222333000144',
  doc_formatado: '11.222.333/0001-44',
  tipo: 'viabilidade',
  valor: '1.075,08',
  modo_valor: 'manual',
  enderecos: itensLote
};
const payloadsMistos = vm.runInContext(
  `montarPayloadsClienteFila(${JSON.stringify(clienteMisto)}, 0, 1)`,
  context
);
assert.deepEqual(Array.from(payloadsMistos, (item) => item.tipo), ['viabilidade', 'agua']);
assert.deepEqual(Array.from(payloadsMistos, (item) => item.valor), ['1.075,08', '2.441,87']);
assert.equal(payloadsMistos[0]._clientBatch.tipo_label, 'Viabilidade');
assert.equal(payloadsMistos[1]._clientBatch.tipo_label, 'Projeto Água');

const abacate = {
  doc: '11222333000144', tipo: 'viabilidade', valor: '1.075,08',
  enderecos: [{...itensLote[0], tipo: undefined, valor: undefined}]
};
const limao = {
  doc: '55666777000188', tipo: 'agua', valor: '2.441,87',
  enderecos: [{...itensLote[1], tipo: undefined, valor: undefined}]
};
const payloadAbacate = vm.runInContext(
  `montarPayloadsClienteFila(${JSON.stringify(abacate)}, 0, 2)[0]`, context
);
const payloadLimao = vm.runInContext(
  `montarPayloadsClienteFila(${JSON.stringify(limao)}, 1, 2)[0]`, context
);
assert.equal(payloadAbacate.tipo, 'viabilidade');
assert.equal(payloadLimao.tipo, 'agua');
assert.notEqual(payloadAbacate.tipo, payloadLimao.tipo, 'CNPJs distintos nao podem herdar o primeiro tipo');

const payloadsLote = vm.runInContext('montarPayloadsLoteAtual()', context);
assert.deepEqual(Array.from(payloadsLote, (item) => item.tipo), ['viabilidade', 'agua']);
assert.deepEqual(Array.from(payloadsLote, (item) => item._batch.tipo), ['viabilidade', 'agua']);

const checkpoint = vm.runInContext(
  `montarCheckpointClienteParaProximo(${JSON.stringify(payloadAbacate)}, {resultado:{cliente:'9001',nome_cliente:'ABACATE'}})`,
  context
);
context.checkpointTeste = checkpoint;
context.payloadMesmoTipo = {...payloadAbacate, endereco: {...payloadAbacate.endereco, empreendimento: 'Abacate 2'}};
context.payloadOutroTipo = {...payloadAbacate, tipo: 'agua'};
context.payloadOutroCnpj = {...payloadAbacate, doc: '55666777000188'};
assert.equal(vm.runInContext('checkpointClienteCompativel(payloadMesmoTipo, checkpointTeste)', context), true);
assert.equal(vm.runInContext('checkpointClienteCompativel(payloadOutroTipo, checkpointTeste)', context), false);
assert.equal(vm.runInContext('checkpointClienteCompativel(payloadOutroCnpj, checkpointTeste)', context), false);

assert.notEqual(
  vm.runInContext(`chaveEnderecoFila(${JSON.stringify(itensLote[0])})`, context),
  vm.runInContext(`chaveEnderecoFila(${JSON.stringify({...itensLote[0], tipo: 'agua'})})`, context),
  'a chave da fila deve distinguir o mesmo endereco com tipos diferentes'
);

async function testarExecucaoFila() {
  const chamadas = [];
  context.executarFluxo = async (payload, options) => {
    chamadas.push({payload: JSON.parse(JSON.stringify(payload)), options: {...options}});
    return {ok: true, resultado: {cliente: `SAP-${chamadas.length}`, nome_cliente: payload.endereco.empreendimento}};
  };

  context.clientesExecucao = [abacate, limao];
  await vm.runInContext('executarFilaClientes(clientesExecucao)', context);
  assert.deepEqual(chamadas.map((item) => item.payload.tipo), ['viabilidade', 'agua']);
  assert.deepEqual(chamadas.map((item) => item.options.resume), [false, false]);

  chamadas.length = 0;
  context.clientesExecucao = [clienteMisto];
  await vm.runInContext('executarFilaClientes(clientesExecucao)', context);
  assert.deepEqual(chamadas.map((item) => item.payload.tipo), ['viabilidade', 'agua']);
  assert.deepEqual(
    chamadas.map((item) => item.options.resume),
    [false, false],
    'mudanca de tipo no mesmo CNPJ deve refazer a validacao XD03'
  );

  console.log('OK: tipos e valores permanecem isolados por empreendimento e por CNPJ.');
}

testarExecucaoFila().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
