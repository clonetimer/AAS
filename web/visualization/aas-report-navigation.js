/* A report link is only valid after a server-owned archive is committed. */
(() => {
  'use strict';
  const managedReport = /^outputs\/experiment_[A-Za-z0-9_-]{1,80}\/reports\/summary\.json$/;

  function renderLink(role, execution) {
    if (!['train', 'eval'].includes(role)) return null;
    const output = document.getElementById(role + 'Output');
    if (!output) return null;
    const old = output.querySelector('.aas-report-action');
    if (old) old.remove();
    const status = execution?.archive_status;
    const path = execution?.summary_path;
    if (status === 'failed') {
      const warning = document.createElement('p');
      warning.className = 'aas-report-action';
      warning.textContent = execution.archive_error || '计算已结束，但实验归档失败。请检查磁盘和服务日志。';
      warning.setAttribute('role', 'alert');
      output.append(warning);
      return null;
    }
    if (status !== 'complete' || typeof path !== 'string' || !managedReport.test(path)) return null;
    const link = document.createElement('a');
    link.className = 'aas-report-action';
    link.href = 'reports.html#' + encodeURIComponent(path);
    link.textContent = '查看本次实验报表';
    output.append(link);
    return link;
  }

  window.AASReportNavigation = Object.freeze({renderLink});
})();
