(function () {
  'use strict';

  var grid = document.getElementById('pluginGrid');
  var search = document.getElementById('pluginSearch'), searchCount = document.getElementById('pluginSearchCount');
  var cachedPlugins = {};
  var pluginChinese = {
    torch_like_cusum:'CUSUM 累积和检测：识别持续的小幅偏差。',
    torch_like_ewma:'EWMA 指数加权平均：识别渐进漂移。',
    torch_like_pca_spe:'PCA 残差检测：识别多参量相关性异常。',
    torch_like_usad:'USAD 双自编码器：基于重构误差检测异常。',
    torch_like_tranad:'TranAD 时序 Transformer：基于时序重构识别异常。'
  };
  function chineseDesc(item){if(pluginChinese[item.name])return pluginChinese[item.name];
    var d=String(item.description||'');if(d.includes('clonetimer'))return '已适配 AAS 的训练、评估与模型保存接口。';return d||'该能力已注册，可在对应功能中配置使用。';}

  var summary = document.getElementById('pluginSummary');
  var typeSel = document.getElementById('pluginType');
  var languageSel = document.getElementById('implementationLanguage');
  var form = document.getElementById('installForm');
  var btn = document.getElementById('installBtn');
  var msg = document.getElementById('installMsg');
  var yamlName = document.getElementById('yamlName');
  var yamlContent = document.getElementById('yamlContent');
  var codeName = document.getElementById('codeName');
  var codeContent = document.getElementById('codeContent');
  var yamlFileInput = document.getElementById('yamlFileInput');
  var codeFileInput = document.getElementById('codeFileInput');
  var codeFileBtn = document.getElementById('codeFileBtn');
  var codeFileBtnText = document.getElementById('codeFileBtnText');
  var libraryFileBtn = document.getElementById('libraryFileBtn');
  var libraryFileInput = document.getElementById('libraryFileInput');
  var nativeLibraryInfo = document.getElementById('nativeLibraryInfo');
  var nativeSecurityNote = document.getElementById('nativeSecurityNote');
  var nativeBuildBox = document.getElementById('nativeBuildBox');
  var nativeCompiler = document.getElementById('nativeCompiler');
  var compileNativeBtn = document.getElementById('compileNativeBtn');
  var nativeBuildStatus = document.getElementById('nativeBuildStatus');
  var codeNameLabel = document.getElementById('codeNameLabel');
  var codeContentLabel = document.getElementById('codeContentLabel');
  var validateBtn = document.getElementById('validatePluginBtn');
  var validationBox = document.getElementById('pluginValidation');
  var codeRequirement = document.getElementById('codeRequirement');
  var templateTitle = document.getElementById('templateTitle');
  var templateNote = document.getElementById('templateNote');
  var restoreTemplateBtn = document.getElementById('restoreTemplateBtn');
  var clearTemplateBtn = document.getElementById('clearTemplateBtn');
  var installDetails = document.getElementById('customPluginInstall');
  var PLUGIN_IMPORT_INTENT_KEY = 'aas.plugin-import-intent.v1';
  var currentTemplateType = '';
  var currentTemplateLanguage = 'python';
  var nativeLibrary = { filename: '', base64: '', size: 0, generated: false, compiler: '' };
  var nativeInfo = {
    abi: 'aas_native_v1', platform: '', library_suffixes: ['.dll', '.so'],
    supported_types: ['detectors', 'metrics'], auto_compile: false, compilers: [], build_isolation: {}
  };

  var INSTALL_TYPES = [
    { value: 'detectors', label: '检测算法' },
    { value: 'metrics', label: '评估指标' },
    { value: 'datasources', label: '数据源' },
    { value: 'preprocessors', label: '预处理算子' },
    { value: 'reports', label: '报告格式' },
    { value: 'runners', label: '执行后端' },
    { value: 'training_strategies', label: '训练策略' },
    { value: 'fault_types', label: '故障类型' }
  ];
  var ORDER = ['detectors', 'fault_types', 'preprocessors', 'training_strategies', 'metrics', 'runners', 'reports', 'datasources'];

  var PLUGIN_CONTRACTS = {
    detectors: '继承 BaseDetector；显式选择 decision_mode；score_threshold 实现 fit+score，native/label 实现对应判定钩子',
    metrics: '继承 BaseMetric；实现 compute(ctx)',
    datasources: '继承 BaseDataSource；实现 read_rows(config)，YAML 还需声明 formats',
    preprocessors: '继承 BasePreprocessOperator；实现 apply(rows, config)',
    reports: '继承 BaseReportRenderer；实现 render(experiment_context, results, out_file)',
    runners: '继承 BaseRunner；实现 run(task_spec)',
    training_strategies: '继承 BaseTrainingStrategy；实现 execute(pipeline, algorithm, data_bundle, algorithm_params, experiment_id)',
    fault_types: '继承 BaseFaultType；实现 inject(data, config)，可复用 apply_to_range(data, config, fn)'
  };
  var INSTALL_EXAMPLES = {
    detectors: {
      yamlName: 'my_detector.yaml',
      yaml: 'name: my_detector\nlabel: 我的检测算法\nclass_path: detectors.my_detector.MyDetector\ntype: machine_learning\ndecision_mode: score_threshold\ndecision_modes: [score_threshold]\ndependencies: [numpy]\nconfig:\n  threshold: 0.5\nconfig_schema:\n  - name: threshold\n    type: float\n    default: 0.5',
      codeName: 'my_detector.py',
      code: "from detectors.base import BaseDetector\n\nclass MyDetector(BaseDetector):\n    default_decision_mode = 'score_threshold'\n    supported_decision_modes = ('score_threshold',)\n\n    def fit(self, train_X, train_y=None, valid_X=None, valid_y=None):\n        return None\n\n    def score(self, test_X):\n        return [0.0 for _ in test_X]"
    },
    metrics: {
      yamlName: 'my_metric.yaml',
      yaml: 'name: my_metric\nlabel: 我的指标\nclass_path: my_metric.MyMetric\ndependencies: []',
      codeName: 'my_metric.py',
      code: "from evaluator.metrics_plugins.base import BaseMetric\n\nclass MyMetric(BaseMetric):\n    name = 'my_metric'\n    def compute(self, ctx):\n        return 0.0"
    },
    datasources: {
      yamlName: 'my_datasource.yaml',
      yaml: 'name: my_datasource\nlabel: 我的数据源\nclass_path: my_datasource.MyDataSource\nformats: [my_format]\ndependencies: []',
      codeName: 'my_datasource.py',
      code: "from evaluator.datasource_plugins.base import BaseDataSource, DataSourceResult\n\nclass MyDataSource(BaseDataSource):\n    def read_rows(self, config):\n        return DataSourceResult(rows=[], meta={})"
    },
    preprocessors: {
      yamlName: 'my_preprocessor.yaml',
      yaml: 'name: my_preprocessor\nlabel: 我的预处理算子\nclass_path: my_preprocessor.MyPreprocessor\ndependencies: []',
      codeName: 'my_preprocessor.py',
      code: "from scripts.core.preproc_plugins.base import BasePreprocessOperator\n\nclass MyPreprocessor(BasePreprocessOperator):\n    def apply(self, rows, config=None):\n        return rows"
    },
    reports: {
      yamlName: 'my_report.yaml',
      yaml: 'name: my_report\nlabel: 我的报告格式\nclass_path: my_report.MyReport\ndependencies: []',
      codeName: 'my_report.py',
      code: "from evaluator.report_plugins.base import BaseReportRenderer\n\nclass MyReport(BaseReportRenderer):\n    def render(self, experiment_context, results, out_file):\n        with open(out_file, 'w', encoding='utf-8') as f:\n            f.write(str(results))"
    },
    runners: {
      yamlName: 'my_runner.yaml',
      yaml: 'name: my_runner\nlabel: 我的执行后端\nclass_path: my_runner.MyRunner\ndependencies: []',
      codeName: 'my_runner.py',
      code: "from runner.runner_plugins.base import BaseRunner\n\nclass MyRunner(BaseRunner):\n    def run(self, task_spec):\n        return {'status': 'success', 'output': {}}"
    },
    training_strategies: {
      yamlName: 'my_strategy.yaml',
      yaml: 'name: my_strategy\nlabel: 我的训练策略\nclass_path: my_strategy.MyStrategy\ndependencies: []',
      codeName: 'my_strategy.py',
      code: "from scripts.core.training_plugins.base import BaseTrainingStrategy\n\nclass MyStrategy(BaseTrainingStrategy):\n    def execute(self, pipeline, algorithm, data_bundle, algorithm_params, experiment_id):\n        return pipeline.run_core(algorithm, data_bundle, algorithm_params, experiment_id)"
    }
,
    fault_types: {
      yamlName: 'my_fault.yaml',
      yaml: 'name: my_fault\nlabel: 我的故障类型\nclass_path: src.fault_plugins.my_fault.MyFaultType\ndescription: 在目标区间按比例降低遥测值\nformula: y′ = y · (1 - loss_ratio)\nuses_severity: false\nsupports_time_range: true\ndependencies: []\nconfig_schema:\n  - name: loss_ratio\n    label: 损失比例\n    type: float\n    default: 0.2\n    min: 0\n    max: 1\n    step: 0.05\n    description: 0.2 表示降低 20%',
      codeName: 'my_fault.py',
      code: "from src.fault_plugins.base import BaseFaultType\n\nclass MyFaultType(BaseFaultType):\n    name = 'my_fault'\n\n    def inject(self, data, config):\n        loss_ratio = float(config.get('loss_ratio', 0.2))\n\n        def apply(row):\n            value = row.get('value')\n            if value is None:\n                return value\n            return value * (1.0 - loss_ratio)\n\n        return self.apply_to_range(data, config, apply)"
    }
  };

  function preferredNativeSuffix() {
    var suffixes = nativeInfo.library_suffixes || [];
    if (suffixes.length) return suffixes[0];
    return /Win/i.test(navigator.platform || '') ? '.dll' : '.so';
  }

  function nativeLibraryName(base) {
    var suffix = preferredNativeSuffix();
    if (suffix === '.so' && base.indexOf('lib') !== 0) return 'lib' + base + suffix;
    return base + suffix;
  }

  function nativeDetectorSource(language) {
    if (language === 'c') {
      return '#include <stddef.h>\n#include <stdlib.h>\n#include <string.h>\n#include <math.h>\n\n#ifdef _WIN32\n#define AAS_EXPORT __declspec(dllexport)\n#else\n#define AAS_EXPORT __attribute__((visibility("default")))\n#endif\n\ntypedef struct { double mean; } detector_t;\nstatic char g_error[256] = "";\n\nAAS_EXPORT const char* aas_plugin_abi(void) { return "aas_native_v1"; }\nAAS_EXPORT const char* aas_plugin_kind(void) { return "detector"; }\nAAS_EXPORT const char* aas_plugin_last_error(void) { return g_error; }\n\nAAS_EXPORT void* aas_detector_create(const char* config_json) {\n    (void)config_json;\n    return calloc(1, sizeof(detector_t));\n}\n\nAAS_EXPORT void aas_detector_destroy(void* instance) { free(instance); }\n\nAAS_EXPORT int aas_detector_fit(void* instance, const double* data, size_t length) {\n    if (!instance || !data || length == 0) {\n        strcpy(g_error, "invalid training data");\n        return 1;\n    }\n    detector_t* detector = (detector_t*)instance;\n    double sum = 0.0;\n    for (size_t i = 0; i < length; ++i) sum += data[i];\n    detector->mean = sum / (double)length;\n    return 0;\n}\n\nAAS_EXPORT int aas_detector_score(void* instance, const double* data, size_t length, double* out_scores) {\n    if (!instance || !data || !out_scores) return 2;\n    detector_t* detector = (detector_t*)instance;\n    for (size_t i = 0; i < length; ++i) out_scores[i] = fabs(data[i] - detector->mean);\n    return 0;\n}\n\nAAS_EXPORT double aas_detector_threshold(void* instance, const double* scores, size_t length) {\n    (void)instance; (void)scores; (void)length;\n    return 0.5;\n}\n\nAAS_EXPORT size_t aas_detector_state_size(void* instance) { (void)instance; return sizeof(double); }\n\nAAS_EXPORT int aas_detector_export_state(void* instance, unsigned char* out, size_t capacity) {\n    if (!instance || !out || capacity < sizeof(double)) return 3;\n    memcpy(out, &((detector_t*)instance)->mean, sizeof(double));\n    return 0;\n}\n\nAAS_EXPORT int aas_detector_import_state(void* instance, const unsigned char* data, size_t length) {\n    if (!instance || !data || length != sizeof(double)) return 4;\n    memcpy(&((detector_t*)instance)->mean, data, sizeof(double));\n    return 0;\n}\n';
    }
    return '#include <cstddef>\n#include <cmath>\n#include <cstring>\n#include <new>\n\n#ifdef _WIN32\n#define AAS_EXPORT __declspec(dllexport)\n#else\n#define AAS_EXPORT __attribute__((visibility("default")))\n#endif\n\nstruct Detector { double mean = 0.0; };\nstatic char g_error[256] = "";\n\nextern "C" {\nAAS_EXPORT const char* aas_plugin_abi() { return "aas_native_v1"; }\nAAS_EXPORT const char* aas_plugin_kind() { return "detector"; }\nAAS_EXPORT const char* aas_plugin_last_error() { return g_error; }\n\nAAS_EXPORT void* aas_detector_create(const char* config_json) {\n    (void)config_json;\n    return new (std::nothrow) Detector();\n}\n\nAAS_EXPORT void aas_detector_destroy(void* instance) { delete static_cast<Detector*>(instance); }\n\nAAS_EXPORT int aas_detector_fit(void* instance, const double* data, std::size_t length) {\n    if (!instance || !data || length == 0) {\n        std::strcpy(g_error, "invalid training data");\n        return 1;\n    }\n    auto* detector = static_cast<Detector*>(instance);\n    double sum = 0.0;\n    for (std::size_t i = 0; i < length; ++i) sum += data[i];\n    detector->mean = sum / static_cast<double>(length);\n    return 0;\n}\n\nAAS_EXPORT int aas_detector_score(void* instance, const double* data, std::size_t length, double* out_scores) {\n    if (!instance || !data || !out_scores) return 2;\n    auto* detector = static_cast<Detector*>(instance);\n    for (std::size_t i = 0; i < length; ++i) out_scores[i] = std::fabs(data[i] - detector->mean);\n    return 0;\n}\n\nAAS_EXPORT double aas_detector_threshold(void*, const double*, std::size_t) { return 0.5; }\nAAS_EXPORT std::size_t aas_detector_state_size(void*) { return sizeof(double); }\n\nAAS_EXPORT int aas_detector_export_state(void* instance, unsigned char* out, std::size_t capacity) {\n    if (!instance || !out || capacity < sizeof(double)) return 3;\n    std::memcpy(out, &static_cast<Detector*>(instance)->mean, sizeof(double));\n    return 0;\n}\n\nAAS_EXPORT int aas_detector_import_state(void* instance, const unsigned char* data, std::size_t length) {\n    if (!instance || !data || length != sizeof(double)) return 4;\n    std::memcpy(&static_cast<Detector*>(instance)->mean, data, sizeof(double));\n    return 0;\n}\n}\n';
  }

  function nativeMetricSource(language) {
    if (language === 'c') {
      return '#include <stddef.h>\n#include <stdint.h>\n\n#ifdef _WIN32\n#define AAS_EXPORT __declspec(dllexport)\n#else\n#define AAS_EXPORT __attribute__((visibility("default")))\n#endif\n\nstatic char g_error[256] = "";\nAAS_EXPORT const char* aas_plugin_abi(void) { return "aas_native_v1"; }\nAAS_EXPORT const char* aas_plugin_kind(void) { return "metric"; }\nAAS_EXPORT const char* aas_plugin_last_error(void) { return g_error; }\n\nAAS_EXPORT double aas_metric_compute(\n    const int32_t* y_true, size_t y_len,\n    const double* anomaly_score, size_t score_len,\n    const int32_t* pred_label, size_t pred_len,\n    const double* train_values, size_t train_len,\n    const double* test_values, size_t test_len,\n    const char* config_json, int* ok) {\n    (void)anomaly_score; (void)score_len;\n    (void)train_values; (void)train_len;\n    (void)test_values; (void)test_len; (void)config_json;\n    if (!ok || !y_true || !pred_label || y_len == 0 || y_len != pred_len) {\n        if (ok) *ok = 0;\n        return 0.0;\n    }\n    size_t correct = 0;\n    for (size_t i = 0; i < y_len; ++i) if (y_true[i] == pred_label[i]) ++correct;\n    *ok = 1;\n    return (double)correct / (double)y_len;\n}\n';
    }
    return '#include <cstddef>\n#include <cstdint>\n\n#ifdef _WIN32\n#define AAS_EXPORT __declspec(dllexport)\n#else\n#define AAS_EXPORT __attribute__((visibility("default")))\n#endif\n\nstatic char g_error[256] = "";\nextern "C" {\nAAS_EXPORT const char* aas_plugin_abi() { return "aas_native_v1"; }\nAAS_EXPORT const char* aas_plugin_kind() { return "metric"; }\nAAS_EXPORT const char* aas_plugin_last_error() { return g_error; }\n\nAAS_EXPORT double aas_metric_compute(\n    const std::int32_t* y_true, std::size_t y_len,\n    const double* anomaly_score, std::size_t score_len,\n    const std::int32_t* pred_label, std::size_t pred_len,\n    const double* train_values, std::size_t train_len,\n    const double* test_values, std::size_t test_len,\n    const char* config_json, int* ok) {\n    (void)anomaly_score; (void)score_len;\n    (void)train_values; (void)train_len;\n    (void)test_values; (void)test_len; (void)config_json;\n    if (!ok || !y_true || !pred_label || y_len == 0 || y_len != pred_len) {\n        if (ok) *ok = 0;\n        return 0.0;\n    }\n    std::size_t correct = 0;\n    for (std::size_t i = 0; i < y_len; ++i) if (y_true[i] == pred_label[i]) ++correct;\n    *ok = 1;\n    return static_cast<double>(correct) / static_cast<double>(y_len);\n}\n}\n';
  }

  function nativeExample(type, language) {
    var cpp = language === 'cpp';
    var ext = cpp ? '.cpp' : '.c';
    if (type === 'detectors') {
      var lib = nativeLibraryName('my_native_detector');
      return {
        yamlName: 'my_native_detector.yaml',
        yaml: 'name: my_native_detector\nlabel: 我的' + (cpp ? 'C++' : 'C') + '检测算法\ndescription: 原生异常检测插件\nlanguage: ' + language + '\nabi: aas_native_v1\nlibrary: ' + lib + '\ntype: native\ndependencies: []\nmin_training_samples: 2\nconfig:\n  threshold: 0.5',
        codeName: 'my_native_detector' + ext,
        code: nativeDetectorSource(language)
      };
    }
    var metricLib = nativeLibraryName('my_native_metric');
    return {
      yamlName: 'my_native_metric.yaml',
      yaml: 'name: my_native_metric\nlabel: 我的' + (cpp ? 'C++' : 'C') + '指标\ndescription: 原生评估指标\nlanguage: ' + language + '\nabi: aas_native_v1\nlibrary: ' + metricLib + '\nrequires: [labels, preds]\ndependencies: []',
      codeName: 'my_native_metric' + ext,
      code: nativeMetricSource(language)
    };
  }

  function isNativeLanguage(language) {
    return language === 'c' || language === 'cpp';
  }

  function nativeTypeSupported(type) {
    return (nativeInfo.supported_types || ['detectors', 'metrics']).indexOf(type) >= 0;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function statusBadge(status) {
    var ok = !status || status.installed;
    var node = el('span', 'pg-badge ' + (ok ? 'pg-ok' : 'pg-warn'));
    node.textContent = ok ? '可用' : '缺依赖';
    if (!ok) node.title = (status.missing || []).join(', ');
    return node;
  }

  function sourceBadge(item) {
    var source = item.source || (item.builtin ? 'builtin' : 'runtime');
    var labels = { builtin: '内置', custom: '自定义', runtime: '运行期' };
    var node = el('span', 'pg-source' + (source === 'custom' ? ' pg-source-custom' : ''), labels[source] || source);
    if (source === 'custom' && item.managed_install) node.title = '由检测算法管理页安装';
    return node;
  }

  function languageBadge(item) {
    var language = String(item.language || 'python').toLowerCase();
    if (language === 'c++') language = 'cpp';
    if (language === 'python' && item.builtin) return null;
    var labels = { python: 'Python', c: 'C', cpp: 'C++' };
    var node = el('span', 'pg-source' + (language !== 'python' ? ' pg-source-custom' : ''), labels[language] || language);
    if (item.abi) node.title = 'ABI: ' + item.abi + (item.runtime_isolation === 'separate_process' ? '；独立进程运行' : '');
    return node;
  }

  function isolationBadge(item) {
    if (!item.native || item.runtime_isolation !== 'separate_process') return null;
    var node = el('span', 'pg-source pg-source-custom', '隔离运行');
    node.title = 'DLL/SO 在独立 Native Plugin Host 进程运行，不加载到 AAS Python 主服务';
    return node;
  }

  async function postUninstall(type, name, force) {
    var response;
    try {
      response = await fetch('/api/plugins/uninstall', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: type, name: name, force: !!force })
      });
    } catch (cause) {
      var disconnected = new Error('无法连接 AAS 服务。请检查服务是否仍在运行、网络连接及服务端日志，然后刷新插件列表确认状态。');
      disconnected.isNetworkFailure = true;
      throw disconnected;
    }
    var result;
    try { result = await response.json(); }
    catch (cause) {
      throw new Error('卸载接口返回了非 JSON 响应（HTTP ' + response.status + '），请查看服务端日志。');
    }
    if (!response.ok || result.error) throw new Error(result.error || ('卸载接口 HTTP ' + response.status));
    return result;
  }

  async function uninstallPlugin(type, item, button) {
    var pluginName = item.descriptor_name || item.name;
    var originalText = button.textContent;
    var forceSubmitted = false;
    button.disabled = true;
    button.textContent = '检查中…';
    try {
      // Never trust a stale frontend catalog for permissions: backend enforces
      // the built-in list again even for direct API callers.
      if (item.builtin || !item.uninstallable) throw new Error('系统内置算法不能卸载。');
      var preview = await postUninstall(type, pluginName, false);
      var displayName = item.label || pluginName;
      var lines = [
        '确认卸载自定义插件“' + displayName + '”（' + pluginName + '）？',
        '',
        '卸载后该插件将不能再用于新的训练、评估或扩展流程；已有历史结果不会删除。'
      ];
      if ((preview.references || []).length) {
        lines.push('', '以下配置仍引用该插件，卸载后相关配置可能无法运行：');
        preview.references.slice(0, 8).forEach(function (path) { lines.push('• ' + path); });
        if (preview.references.length > 8) lines.push('• 另有 ' + (preview.references.length - 8) + ' 个配置');
      }
      if (preview.language === 'c' || preview.language === 'cpp') {
        if (preview.library_will_remove) lines.push('', '检测算法管理器安装的原生 DLL/SO 会一并删除。');
        else if (preview.managed_library) lines.push('', '原生库仍被其他描述引用，将保留文件。');
        if (preview.source_will_remove) lines.push('留存的 C/C++ 源文件也会一并删除。');
      } else if (preview.code_will_remove) {
        lines.push('', '检测算法管理器安装的 Python 实现文件也会一并删除。');
      } else if (preview.managed_code) {
        lines.push('', 'Python 实现仍被其他插件引用，将保留实现文件。');
      } else {
        lines.push('', '未确认由检测算法管理器创建的 Python 实现文件将保留，不会误删。');
      }
      if (!window.confirm(lines.join('\n'))) return;

      button.textContent = '卸载中…';
      forceSubmitted = true;
      var result = await postUninstall(type, pluginName, true);
      button.textContent = result.uninstalled ? '已卸载' : originalText;
      await loadPlugins();
    } catch (e) {
      if (forceSubmitted && e.isNetworkFailure) {
        // A response can be lost AFTER the server committed the deletion. Do
        // not retry an irreversible request or falsely claim it failed.
        try {
          var statusResponse = await fetch('/api/plugins', {credentials:'same-origin'});
          if (statusResponse.ok) {
            var statusData = await statusResponse.json();
            if (!statusData.error) {
              render(statusData.plugins || {});
              var remaining = ((statusData.plugins || {})[type] || {}).items || [];
              var stillPresent = remaining.some(function (p) {
                return p.name === item.name || p.descriptor_name === pluginName;
              });
              window.alert(stillPresent
                ? '卸载请求连接中断，插件仍在列表中。请检查服务端日志后再操作。'
                : '卸载请求连接中断，但插件已不在目录中，可能已卸载成功。请刷新确认。');
              return;
            }
          }
        } catch (ignored) { /* Original network diagnostic is more useful. */ }
      }
      window.alert('卸载未完成确认：' + e.message);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  function renderSummary(data) {
    var total = 0;
    var ready = 0;
    Object.keys(data).forEach(function (type) {
      (data[type].items || []).forEach(function (item) {
        total += 1;
        if (!item.dependencies_status || item.dependencies_status.installed) ready += 1;
      });
    });
    var values = summary.querySelectorAll('.pg-summary-value');
    values[0].textContent = String(total);
    values[1].textContent = String(ready);
    values[2].textContent = String(total - ready);
  }

  function render(data) {
    cachedPlugins=data;grid.innerHTML = '';
    var keyword=search.value.trim().toLowerCase(),matching=0;
    renderSummary(data);
    ORDER.forEach(function (type) {
      var group = data[type];
      if (!group) return;
      var allItems=group.items||[];
      var items=allItems.filter(function(item){return !keyword||[item.label,item.name,item.description,chineseDesc(item)].join(' ').toLowerCase().includes(keyword);});
      matching+=items.length;
      var integration = group.integration || {};
      var card = el('article', 'pg-card');
      var head = el('div', 'pg-card-head');
      head.appendChild(el('h3', '', group.label || type));
      head.appendChild(el('span', 'pg-count', items.length===allItems.length?String(items.length):items.length+' / '+allItems.length));
      if (integration.entry) {
        var route = integration.entry.replace(/\.html$/, '');
        var link = el('a', 'pg-entry', '去使用 →');
        link.href = 'index.html#' + route;
        link.target = '_top';
        head.appendChild(link);
      }
      card.appendChild(head);

      var impact = el('div', 'pg-impact');
      var hook = el('span');
      hook.appendChild(el('strong', '', integration.hook || '扩展点'));
      impact.appendChild(hook);
      impact.appendChild(el('span', '', integration.scope || ''));
      impact.appendChild(el('span', '', integration.used_by || ''));
      card.appendChild(impact);
      var ready=allItems.filter(function(item){return !item.dependencies_status||item.dependencies_status.installed;}).length;
      var track=el('div','pg-ready-track'),fill=el('div','pg-ready-fill');fill.style.width=(allItems.length?100*ready/allItems.length:0)+'%';track.appendChild(fill);card.appendChild(track);
      card.appendChild(el('div','pg-ready-copy','就绪 '+ready+' / '+allItems.length+' · '+(allItems.length?Math.round(100*ready/allItems.length):0)+'%'));
      var list = el('div', 'pg-items');
      if (!items.length) {
        list.appendChild(el('div', 'pg-item pg-desc', '暂无已注册能力'));
      }
      items.forEach(function (item) {
        var row = el('div', 'pg-item');
        var nameRow = el('div', 'pg-name-row');
        var name = el('div', 'pg-name', item.label || item.name || '未命名');
        var code = el('code', '', item.name || '');
        name.appendChild(code);
        nameRow.appendChild(name);
        nameRow.appendChild(sourceBadge(item));
        var langBadge = languageBadge(item);
        if (langBadge) nameRow.appendChild(langBadge);
        var isoBadge = isolationBadge(item);
        if (isoBadge) nameRow.appendChild(isoBadge);
        nameRow.appendChild(statusBadge(item.dependencies_status));
        row.appendChild(nameRow);
        row.appendChild(el('div', 'pg-desc', chineseDesc(item)));
        if (item.uninstallable) {
          var actions = el('div', 'pg-actions');
          var uninstallBtn = el('button', 'pg-uninstall', '卸载');
          uninstallBtn.type = 'button';
          uninstallBtn.title = '卸载该自定义插件';
          uninstallBtn.addEventListener('click', function () {
            uninstallPlugin(type, item, uninstallBtn);
          });
          actions.appendChild(uninstallBtn);
          row.appendChild(actions);
        }
        list.appendChild(row);
      });
      card.appendChild(list);
      grid.appendChild(card);
    });
    searchCount.textContent=`匹配 ${matching} 项` ;
  }

  search.addEventListener('input',function(){render(cachedPlugins);});
  function loadPlugins() {
    grid.innerHTML = '<div class="pg-loading">加载中…</div>';
    return fetch('/api/plugins')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) throw new Error(d.error);
        render(d.plugins || {});
      })
      .catch(function (e) {
        grid.innerHTML = '';
        grid.appendChild(el('div', 'pg-loading', '加载失败：' + e.message));
      });
  }

  INSTALL_TYPES.forEach(function (t) {
    var opt = document.createElement('option');
    opt.value = t.value;
    opt.textContent = t.label;
    typeSel.appendChild(opt);
  });

  function installTypeLabel(type) {
    var item = INSTALL_TYPES.find(function (entry) { return entry.value === type; });
    return item ? item.label : type;
  }

  function installExample(type, language) {
    language = language || languageSel.value || 'python';
    if (isNativeLanguage(language)) return nativeExample(type, language);
    return INSTALL_EXAMPLES[type] || INSTALL_EXAMPLES.metrics;
  }

  function normalizeTemplateText(value) {
    return String(value || '').replace(/\r\n/g, '\n').trim();
  }

  function isEditorEmpty() {
    return !yamlName.value.trim() && !yamlContent.value.trim() && !codeName.value.trim() && !codeContent.value.trim();
  }

  function isCurrentDefaultTemplate(type, language) {
    language = language || languageSel.value || 'python';
    var example = installExample(type, language);
    var textMatches = yamlName.value.trim() === example.yamlName &&
      normalizeTemplateText(yamlContent.value) === normalizeTemplateText(example.yaml) &&
      codeName.value.trim() === example.codeName &&
      normalizeTemplateText(codeContent.value) === normalizeTemplateText(example.code);
    return textMatches;
  }

  function resetImportedFiles() {
    if (yamlFileInput) yamlFileInput.value = '';
    if (codeFileInput) codeFileInput.value = '';
    if (libraryFileInput) libraryFileInput.value = '';
    nativeLibrary = { filename: '', base64: '', size: 0, generated: false, compiler: '' };
  }

  function languageLabel(language) {
    return ({ python: 'Python', c: 'C', cpp: 'C++' })[language] || language;
  }

  function updateLanguageAvailability() {
    var supported = nativeTypeSupported(typeSel.value);
    Array.prototype.forEach.call(languageSel.options, function (option) {
      if (option.value === 'python') return;
      option.disabled = !supported;
    });
    if (!supported && isNativeLanguage(languageSel.value)) languageSel.value = 'python';
  }

  function availableCompilers(language) {
    return (nativeInfo.compilers || []).filter(function (item) {
      return (item.languages || []).indexOf(language) >= 0;
    });
  }

  function updateCompilerOptions() {
    if (!nativeCompiler) return;
    var language = languageSel.value;
    var previous = nativeCompiler.value || 'auto';
    var tools = isNativeLanguage(language) ? availableCompilers(language) : [];
    nativeCompiler.innerHTML = '';
    var auto = document.createElement('option');
    auto.value = 'auto';
    auto.textContent = tools.length ? '自动选择可用工具链' : '未检测到可用系统编译器';
    nativeCompiler.appendChild(auto);
    tools.forEach(function (tool) {
      var option = document.createElement('option');
      option.value = tool.id;
      option.textContent = (tool.label || tool.id) + (tool.version ? ' · ' + tool.version : '');
      nativeCompiler.appendChild(option);
    });
    if (Array.prototype.some.call(nativeCompiler.options, function (o) { return o.value === previous; })) {
      nativeCompiler.value = previous;
    } else {
      nativeCompiler.value = 'auto';
    }
    nativeCompiler.disabled = !tools.length;
    if (compileNativeBtn) compileNativeBtn.disabled = !tools.length || !isNativeLanguage(language);
    if (nativeBuildStatus && isNativeLanguage(language)) {
      nativeBuildStatus.className = 'pg-native-build-status';
      nativeBuildStatus.textContent = tools.length
        ? '已检测到 ' + tools.length + ' 套独立原生工具链；编译过程不继承 Python/venv/Conda 依赖。'
        : '未检测到系统编译器。可安装 GCC/Clang/Visual Studio Build Tools，或直接导入已编译 DLL/SO。';
    }
  }

  function clearGeneratedLibraryBecauseSourceChanged() {
    if (!nativeLibrary.generated) return false;
    nativeLibrary = { filename: '', base64: '', size: 0, generated: false, compiler: '' };
    if (libraryFileInput) libraryFileInput.value = '';
    updateLibraryInfo();
    if (nativeBuildStatus) {
      nativeBuildStatus.className = 'pg-native-build-status';
      nativeBuildStatus.textContent = '源码已修改，之前由 AAS 生成的动态库已作废，请重新编译。';
    }
    return true;
  }

  function updateTemplateStatus() {
    var type = typeSel.value;
    var language = languageSel.value;
    var label = installTypeLabel(type);
    if (templateTitle) templateTitle.textContent = '示例模板：最小可运行' + label + ' · ' + languageLabel(language);
    if (!templateNote) return;
    if (isEditorEmpty()) {
      templateNote.textContent = '当前编辑区为空，可点击“恢复示例”重新载入最小模板。';
    } else if (isCurrentDefaultTemplate(type, language)) {
      templateNote.textContent = isNativeLanguage(language)
        ? '当前为可编辑原生源码模板。可直接修改，并可用 AAS 的独立原生工具链编译；也可导入已编译动态库。'
        : '当前内容为可编辑示例模板，可直接修改文件名、插件名称和实现逻辑。';
    } else {
      templateNote.textContent = '当前内容已基于示例修改；如需重新开始，可点击“恢复示例”。';
    }
  }

  function nativeCompileHint(language) {
    var suffix = preferredNativeSuffix();
    var win = suffix === '.dll';
    if (win) {
      if (language === 'c') return 'Windows 示例：cl /LD my_native_metric.c /Fe:my_native_metric.dll';
      return 'Windows 示例：cl /LD /EHsc my_native_metric.cpp /Fe:my_native_metric.dll';
    }
    if (language === 'c') return 'Linux 示例：gcc -shared -fPIC my_native_metric.c -o libmy_native_metric.so';
    return 'Linux 示例：g++ -shared -fPIC my_native_metric.cpp -o libmy_native_metric.so';
  }

  function updateLibraryInfo() {
    if (!nativeLibraryInfo) return;
    if (!isNativeLanguage(languageSel.value)) {
      nativeLibraryInfo.className = 'pg-library-info';
      nativeLibraryInfo.textContent = '';
      return;
    }
    nativeLibraryInfo.className = 'pg-library-info show';
    if (nativeLibrary.filename) {
      var origin = nativeLibrary.generated
        ? 'AAS 本机隔离编译生成' + (nativeLibrary.compiler ? ' · ' + nativeLibrary.compiler : '')
        : '手工导入';
      nativeLibraryInfo.textContent = '当前原生库：' + nativeLibrary.filename + '（' + Math.max(1, Math.round(nativeLibrary.size / 1024)) + ' KB，' + origin + '）。解析校验会在隔离进程检查 ABI 与必需导出函数；安装后由独立 Native Plugin Host 执行。';
    } else {
      nativeLibraryInfo.textContent = '尚未选择原生库。当前服务要求：' + (nativeInfo.library_suffixes || []).join(' / ') + '；ABI：' + (nativeInfo.abi || 'aas_native_v1') + '。';
    }
  }

  function updateInstallGuidance() {
    var type = typeSel.value;
    var language = languageSel.value;
    var label = installTypeLabel(type);
    var native = isNativeLanguage(language);
    if (nativeSecurityNote) nativeSecurityNote.className = 'pg-native-note' + (native ? ' show' : '');
    if (nativeBuildBox) nativeBuildBox.className = 'pg-native-build' + (native ? ' show' : '');
    if (libraryFileBtn) libraryFileBtn.style.display = native ? 'inline-flex' : 'none';
    updateCompilerOptions();

    if (native) {
      codeFileBtnText.textContent = '导入 ' + languageLabel(language) + ' 源码';
      codeFileInput.accept = language === 'c' ? '.c,text/x-c' : '.cpp,.cc,.cxx,text/x-c++src';
      codeNameLabel.textContent = languageLabel(language) + ' 源文件名（本机编译时必需；仅导入动态库时可选）';
      codeContentLabel.textContent = languageLabel(language) + ' 源码（本机编译时必需；仅导入动态库时可选）';
      if (codeRequirement) {
        codeRequirement.textContent = label + '支持 ' + languageLabel(language) + ' 原生实现，使用稳定 C ABI ' + (nativeInfo.abi || 'aas_native_v1') + '。C++ 必须用 extern "C" 导出接口。可点击“编译当前源码”由 AAS 调用独立系统工具链生成 DLL/SO，也可直接“导入已编译库”。编译不会使用 Python 包、venv/Conda 头文件或用户 CFLAGS/LDFLAGS；运行时 DLL/SO 由独立 Native Plugin Host 进程执行。若本机没有编译器，可使用外部编译。' + nativeCompileHint(language);
      }
    } else {
      codeFileBtnText.textContent = '导入 Python 实现';
      codeFileInput.accept = '.py,text/x-python';
      codeNameLabel.textContent = 'Python 文件名（条件必需）';
      codeContentLabel.textContent = 'Python 代码（条件必需）';
      if (codeRequirement) {
        codeRequirement.textContent = label + '插件需' + (PLUGIN_CONTRACTS[type] || '实现对应插件契约') + '。下面已提供一个最小可运行示例，可直接修改文件名、插件名称和实现逻辑。新增 class_path 时需提供 Python 文件；复用当前环境已有 class_path 时可省略。';
      }
    }
    updateLibraryInfo();
    updateTemplateStatus();
  }

  function applyInstallExample(type, language, options) {
    language = language || languageSel.value || 'python';
    var example = installExample(type, language);
    options = options || {};
    yamlName.value = example.yamlName;
    yamlContent.value = example.yaml;
    codeName.value = example.codeName;
    codeContent.value = example.code;
    currentTemplateType = type;
    currentTemplateLanguage = language;
    resetImportedFiles();
    updateInstallGuidance();
    if (options.clearValidation !== false) {
      clearValidation(options.note || '已载入“' + installTypeLabel(type) + ' · ' + languageLabel(language) + '”最小示例，可直接修改后解析并校验。');
    }
  }

  function buildPayload() {
    var language = languageSel.value;
    var payload = {
      type: typeSel.value,
      language: language,
      yaml_filename: yamlName.value.trim(),
      yaml_content: yamlContent.value,
      code_filename: '',
      code_content: '',
      source_filename: '',
      source_content: '',
      library_filename: '',
      library_content_base64: ''
    };
    if (isNativeLanguage(language)) {
      payload.source_filename = codeName.value.trim();
      payload.source_content = codeContent.value;
      payload.library_filename = nativeLibrary.filename;
      payload.library_content_base64 = nativeLibrary.base64;
    } else {
      payload.code_filename = codeName.value.trim();
      payload.code_content = codeContent.value;
    }
    return payload;
  }

  function clearValidation(note) {
    validationBox.className = 'pg-validation';
    validationBox.textContent = '';
    if (note) {
      validationBox.classList.add('show');
      validationBox.appendChild(el('div', 'pg-validation-row', note));
    }
  }

  function renderValidation(result) {
    validationBox.innerHTML = '';
    validationBox.className = 'pg-validation show ' + (result.valid ? 'ok' : 'bad');
    validationBox.appendChild(el('div', 'pg-validation-title', result.valid ? '✓ 插件内容校验通过' : '✕ 插件内容存在问题'));

    var d = result.descriptor || {};
    if (d.name || d.class_path || d.language) {
      validationBox.appendChild(el('div', 'pg-validation-row', '名称：' + (d.name || '-')));
      if (d.language === 'c' || d.language === 'cpp') {
        validationBox.appendChild(el('div', 'pg-validation-row', '实现：' + languageLabel(d.language) + ' / ' + (d.abi || 'aas_native_v1')));
        validationBox.appendChild(el('div', 'pg-validation-row', '动态库：' + (d.library_filename || d.library || '-')));
      } else {
        validationBox.appendChild(el('div', 'pg-validation-row', '类路径：' + (d.class_path || '-')));
      }
      validationBox.appendChild(el('div', 'pg-validation-row', '依赖：' + ((d.dependencies || []).join(', ') || '无')));
      if (d.config_keys && d.config_keys.length) {
        validationBox.appendChild(el('div', 'pg-validation-row', '参数：' + d.config_keys.join(', ')));
      }
    }
    (result.errors || []).forEach(function (item) {
      validationBox.appendChild(el('div', 'pg-validation-error', '错误：' + item));
    });
    (result.warnings || []).forEach(function (item) {
      validationBox.appendChild(el('div', 'pg-validation-warn', '提示：' + item));
    });
    var nativeResult = result.native || {};
    if (nativeResult.provided) {
      validationBox.appendChild(el('div', 'pg-validation-row', '原生库：ABI/导出函数校验通过' + (nativeResult.kind ? '；类型 ' + nativeResult.kind : '')));
    } else {
      var code = result.code || {};
      if (code.provided) {
        validationBox.appendChild(el('div', 'pg-validation-row', 'Python：' + (code.syntax_ok === false ? '语法/加载失败' : '语法通过') + (code.class_name ? '；类 ' + code.class_name : '')));
      } else if (code.resolved_existing_class) {
        validationBox.appendChild(el('div', 'pg-validation-row', 'Python：未上传源码，复用当前环境已有 class_path 实现'));
      }
    }
  }

  async function previewPlugin(showProgress) {
    var payload = buildPayload();
    if (!payload.yaml_filename || !payload.yaml_content.trim()) {
      clearValidation('请先导入或填写 YAML 描述文件。');
      return { valid: false };
    }
    if (showProgress !== false) {
      validateBtn.disabled = true;
      validateBtn.textContent = '校验中…';
    }
    try {
      var response = await fetch('/api/plugins/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok || result.error) throw new Error(result.error || ('HTTP ' + response.status));
      renderValidation(result);
      return result;
    } catch (e) {
      renderValidation({ valid: false, errors: ['校验请求失败：' + e.message] });
      return { valid: false };
    } finally {
      if (showProgress !== false) {
        validateBtn.disabled = false;
        validateBtn.textContent = '解析并校验';
      }
    }
  }

  async function loadTextFile(file, nameInput, contentInput) {
    if (!file) return;
    var text = await file.text();
    nameInput.value = file.name;
    contentInput.value = text;
    updateTemplateStatus();
    clearValidation('文件已读取，正在解析…');
    await previewPlugin(false);
  }

  function confirmTemplateReplace(nextType, nextLanguage) {
    var previousType = currentTemplateType || typeSel.value;
    var previousLanguage = currentTemplateLanguage || languageSel.value;
    var canReplace = !nativeLibrary.filename && (isEditorEmpty() || isCurrentDefaultTemplate(previousType, previousLanguage));
    if (canReplace) return true;
    return window.confirm(
      '切换插件类型或实现语言会用“' + installTypeLabel(nextType) + ' · ' + languageLabel(nextLanguage) + '”示例模板替换当前编辑内容。\n\n未保存的修改将丢失，是否继续？'
    );
  }

  typeSel.addEventListener('change', function () {
    var nextType = typeSel.value;
    var previousType = currentTemplateType || nextType;
    var previousLanguage = currentTemplateLanguage || languageSel.value;
    updateLanguageAvailability();
    var nextLanguage = languageSel.value;
    if (!confirmTemplateReplace(nextType, nextLanguage)) {
      typeSel.value = previousType;
      languageSel.value = previousLanguage;
      updateLanguageAvailability();
      updateInstallGuidance();
      return;
    }
    applyInstallExample(nextType, nextLanguage, {
      note: '已切换为“' + installTypeLabel(nextType) + ' · ' + languageLabel(nextLanguage) + '”示例模板，可直接修改。'
    });
  });

  languageSel.addEventListener('change', function () {
    var nextLanguage = languageSel.value;
    var previousLanguage = currentTemplateLanguage || 'python';
    var type = typeSel.value;
    if (isNativeLanguage(nextLanguage) && !nativeTypeSupported(type)) {
      window.alert('C/C++ 原生插件首期仅支持“检测算法”和“评估指标”。');
      languageSel.value = previousLanguage;
      return;
    }
    if (!confirmTemplateReplace(type, nextLanguage)) {
      languageSel.value = previousLanguage;
      updateInstallGuidance();
      return;
    }
    applyInstallExample(type, nextLanguage, {
      note: '已切换为“' + installTypeLabel(type) + ' · ' + languageLabel(nextLanguage) + '”示例模板。'
    });
  });

  restoreTemplateBtn.addEventListener('click', function () {
    var type = typeSel.value;
    var language = languageSel.value;
    if (isCurrentDefaultTemplate(type, language)) {
      updateInstallGuidance();
      return;
    }
    if (!isEditorEmpty() || nativeLibrary.filename) {
      var ok = window.confirm('恢复示例会覆盖当前编辑内容并清除已选择的原生库。\n\n是否恢复“' + installTypeLabel(type) + ' · ' + languageLabel(language) + '”最小示例？');
      if (!ok) return;
    }
    applyInstallExample(type, language, { note: '已恢复“' + installTypeLabel(type) + ' · ' + languageLabel(language) + '”最小示例。' });
  });

  clearTemplateBtn.addEventListener('click', function () {
    if ((!isEditorEmpty() || nativeLibrary.filename) && !isCurrentDefaultTemplate(typeSel.value, languageSel.value)) {
      var ok = window.confirm('清空当前编辑内容和已选择的原生库？\n\n未保存的修改将丢失。');
      if (!ok) return;
    }
    yamlName.value = '';
    yamlContent.value = '';
    codeName.value = '';
    codeContent.value = '';
    resetImportedFiles();
    updateInstallGuidance();
    clearValidation('编辑区已清空；可从零编写，或点击“恢复示例”重新载入模板。');
    yamlName.focus();
  });

  yamlFileInput.addEventListener('change', function () {
    loadTextFile(yamlFileInput.files && yamlFileInput.files[0], yamlName, yamlContent)
      .catch(function (e) { renderValidation({ valid: false, errors: ['读取 YAML 失败：' + e.message] }); });
  });
  codeFileInput.addEventListener('change', function () {
    var label = isNativeLanguage(languageSel.value) ? languageLabel(languageSel.value) + ' 源码' : 'Python';
    loadTextFile(codeFileInput.files && codeFileInput.files[0], codeName, codeContent)
      .catch(function (e) { renderValidation({ valid: false, errors: ['读取 ' + label + ' 失败：' + e.message] }); });
  });

  async function fileToBase64(file) {
    var bytes = new Uint8Array(await file.arrayBuffer());
    var binary = '';
    var chunk = 0x8000;
    for (var i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + chunk, bytes.length)));
    }
    return btoa(binary);
  }

  async function compileNativeSource() {
    var language = languageSel.value;
    if (!isNativeLanguage(language)) return;
    var sourceFilename = codeName.value.trim();
    var source = codeContent.value;
    if (!sourceFilename || !source.trim()) {
      if (nativeBuildStatus) {
        nativeBuildStatus.className = 'pg-native-build-status bad';
        nativeBuildStatus.textContent = '请先填写或导入 C/C++ 源文件名和源码。';
      }
      return;
    }
    if (!availableCompilers(language).length) {
      if (nativeBuildStatus) {
        nativeBuildStatus.className = 'pg-native-build-status bad';
        nativeBuildStatus.textContent = '当前未检测到支持 ' + languageLabel(language) + ' 的系统编译器，请改用“导入已编译库”。';
      }
      return;
    }
    compileNativeBtn.disabled = true;
    compileNativeBtn.textContent = '编译中…';
    if (nativeBuildStatus) {
      nativeBuildStatus.className = 'pg-native-build-status';
      nativeBuildStatus.textContent = '正在独立临时目录中调用系统编译器；不会继承 Python/venv/Conda 编译依赖。';
    }
    try {
      var response = await fetch('/api/plugins/native-build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: typeSel.value,
          language: language,
          source_filename: sourceFilename,
          source_content: source,
          toolchain: nativeCompiler && nativeCompiler.value ? nativeCompiler.value : 'auto'
        })
      });
      var result = await response.json();
      if (!response.ok || result.error || !result.built) {
        throw new Error(result.error || '编译未生成动态库');
      }
      nativeLibrary = {
        filename: result.library_filename,
        base64: result.library_content_base64,
        size: result.size_bytes || 0,
        generated: true,
        compiler: (result.compiler && result.compiler.label) || ''
      };
      if (libraryFileInput) libraryFileInput.value = '';
      updateLibraryInfo();
      if (nativeBuildStatus) {
        nativeBuildStatus.className = 'pg-native-build-status ok';
        nativeBuildStatus.textContent = '编译成功：' + result.library_filename + ' · ' + ((result.compiler && result.compiler.label) || '系统工具链') + '；已在短生命周期隔离进程中通过 ABI/导出函数校验。';
      }
      clearValidation('编译成功，正在继续校验 YAML 与动态库…');
      await previewPlugin(false);
    } catch (e) {
      nativeLibrary = { filename: '', base64: '', size: 0, generated: false, compiler: '' };
      updateLibraryInfo();
      if (nativeBuildStatus) {
        nativeBuildStatus.className = 'pg-native-build-status bad';
        nativeBuildStatus.textContent = e.message;
      }
      renderValidation({ valid: false, errors: ['C/C++ 编译失败：' + e.message] });
    } finally {
      compileNativeBtn.textContent = '编译当前源码';
      compileNativeBtn.disabled = !availableCompilers(languageSel.value).length || !isNativeLanguage(languageSel.value);
    }
  }

  libraryFileInput.addEventListener('change', async function () {
    var file = libraryFileInput.files && libraryFileInput.files[0];
    if (!file) return;
    try {
      nativeLibrary = {
        filename: file.name,
        base64: await fileToBase64(file),
        size: file.size,
        generated: false,
        compiler: ''
      };
      updateLibraryInfo();
      updateTemplateStatus();
      if (nativeBuildStatus) {
        nativeBuildStatus.className = 'pg-native-build-status';
        nativeBuildStatus.textContent = '已使用手工导入的动态库；无需执行 AAS 本机编译。';
      }
      clearValidation('原生库已读取，请点击“解析并校验”检查 ABI 与导出函数。');
    } catch (e) {
      nativeLibrary = { filename: '', base64: '', size: 0, generated: false, compiler: '' };
      updateLibraryInfo();
      renderValidation({ valid: false, errors: ['读取原生库失败：' + e.message] });
    }
  });

  validateBtn.addEventListener('click', function () { previewPlugin(true); });
  if (compileNativeBtn) compileNativeBtn.addEventListener('click', compileNativeSource);

  [yamlName, yamlContent, codeName, codeContent].forEach(function (field) {
    field.addEventListener('input', function () {
      var generatedCleared = false;
      if (isNativeLanguage(languageSel.value) && (field === codeName || field === codeContent)) {
        generatedCleared = clearGeneratedLibraryBecauseSourceChanged();
      }
      updateTemplateStatus();
      clearValidation(generatedCleared
        ? '源码已修改，AAS 编译生成的动态库已作废；请重新编译后再校验。'
        : '内容已修改，请重新解析并校验。');
    });
  });

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    msg.textContent = '';
    btn.disabled = true;
    btn.textContent = '校验中…';
    try {
      var validation = await previewPlugin(false);
      if (!validation.valid) {
        msg.style.color = 'var(--danger)';
        msg.textContent = '未安装：请先修正上方校验错误。';
        return;
      }

      if (isCurrentDefaultTemplate(typeSel.value, languageSel.value)) {
        var installExampleAnyway = window.confirm(
          '当前内容仍是“' + installTypeLabel(typeSel.value) + ' · ' + languageLabel(languageSel.value) + '”默认示例模板。\n\n' +
          '建议先修改插件名称、文件名或实现逻辑，避免把示例插件直接安装到系统中。\n\n' +
          '仍要安装这个示例插件吗？'
        );
        if (!installExampleAnyway) {
          msg.style.color = 'var(--muted)';
          msg.textContent = '已取消安装，请先在示例基础上完成修改。';
          return;
        }
      }

      btn.textContent = '安装中…';
      var response = await fetch('/api/plugins/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload())
      });
      var d = await response.json();
      if (!response.ok || d.error) throw new Error(d.error || ('HTTP ' + response.status));
      if (d.registered) {
        msg.style.color = 'var(--success)';
        msg.textContent = '已安装并进入运行期：' + (d.plugin_name || '插件') + (d.language ? ' · ' + languageLabel(d.language) : '');
      } else {
        msg.style.color = 'var(--warning)';
        msg.textContent = (d.rolled_back ? '安装失败，已回滚：' : '未进入运行期：') +
          (d.registration_error || '请检查插件实现');
      }
      await loadPlugins();
    } catch (e) {
      msg.style.color = 'var(--danger)';
      msg.textContent = '请求失败：' + e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = '安装并注册';
    }
  });

  function requestedInstallIntent() {
    var query = new URLSearchParams(window.location.search || '');
    var queryType = query.get('install') || query.get('type') || '';
    var queryLanguage = query.get('language') || '';
    var querySource = query.get('source') || '';
    var stored = null;

    try {
      var raw = window.sessionStorage.getItem(PLUGIN_IMPORT_INTENT_KEY);
      if (raw) {
        window.sessionStorage.removeItem(PLUGIN_IMPORT_INTENT_KEY);
        stored = JSON.parse(raw);
      }
    } catch (error) {
      console.warn('读取插件导入跳转状态失败。', error);
    }

    var intent = stored && typeof stored === 'object' ? stored : {};
    if (queryType) intent.type = queryType;
    if (queryLanguage) intent.language = queryLanguage;
    if (querySource) intent.source = querySource;
    if (queryType) intent.openInstall = true;

    var validType = INSTALL_TYPES.some(function (item) { return item.value === intent.type; });
    if (!validType) return null;

    var language = String(intent.language || 'python').toLowerCase();
    if (language === 'c++') language = 'cpp';
    if (['python', 'c', 'cpp'].indexOf(language) < 0) language = 'python';

    return {
      type: intent.type,
      language: language,
      openInstall: intent.openInstall !== false,
      source: intent.source || ''
    };
  }

  function focusRequestedInstall(intent) {
    if (!intent) return;

    typeSel.value = intent.type;
    updateLanguageAvailability();
    languageSel.value = intent.language;
    updateLanguageAvailability();

    // “智能检测算法评估 → 导入算法”保持原 Python 使用习惯；用户进入检测算法管理后仍可
    // 主动切换 C/C++。其他深链若请求了支持的原生语言，则按请求值显示。
    if (isNativeLanguage(languageSel.value) && !nativeTypeSupported(typeSel.value)) {
      languageSel.value = 'python';
      updateLanguageAvailability();
    }

    applyInstallExample(typeSel.value, languageSel.value, {
      clearValidation: false,
      note: intent.source === 'runner'
        ? '已从“智能检测算法评估”进入检测算法管理，当前已定位到“检测算法”导入。'
        : ''
    });

    if (intent.openInstall && installDetails) {
      installDetails.open = true;
      window.requestAnimationFrame(function () {
        installDetails.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }

    if (intent.source === 'runner') {
      clearValidation('已从“智能检测算法评估”跳转到统一插件导入入口。当前类型为“检测算法”，默认使用 Python；如需 C/C++，可在“实现语言”中切换。');
    }
  }

  async function loadNativeInfo() {
    try {
      var response = await fetch('/api/plugins/native-info');
      var data = await response.json();
      if (response.ok && !data.error) nativeInfo = Object.assign(nativeInfo, data);
    } catch (e) {
      // Keep conservative browser-side defaults; normal Python plugins remain usable.
    }
  }

  (async function init() {
    var intent = requestedInstallIntent();
    await loadNativeInfo();
    updateLanguageAvailability();
    if (intent) {
      focusRequestedInstall(intent);
    } else {
      applyInstallExample(typeSel.value, languageSel.value, { clearValidation: false });
    }
    loadPlugins();
  })();
})();
