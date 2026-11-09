#ifndef AAS_NATIVE_V1_H
#define AAS_NATIVE_V1_H

#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#  define AAS_EXPORT __declspec(dllexport)
#else
#  define AAS_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Common */
AAS_EXPORT const char* aas_plugin_abi(void);       /* must return "aas_native_v1" */
AAS_EXPORT const char* aas_plugin_kind(void);      /* "detector" or "metric" */
AAS_EXPORT const char* aas_plugin_last_error(void);/* optional but recommended */

/* Detector kind */
AAS_EXPORT void*  aas_detector_create(const char* config_json);
AAS_EXPORT void   aas_detector_destroy(void* instance);
AAS_EXPORT int    aas_detector_fit(void* instance, const double* data, size_t length);
/* score is required for decision_mode=score_threshold; optional for label mode */
AAS_EXPORT int    aas_detector_score(void* instance, const double* data, size_t length, double* out_scores);
/* optional final-label callback for decision_mode=native or label; 0=normal, nonzero=anomaly */
AAS_EXPORT int    aas_detector_predict(void* instance, const double* data, size_t length, int32_t* out_labels);
/* optional algorithm-owned cutoff for decision_mode=native when predict is omitted */
AAS_EXPORT double aas_detector_threshold(void* instance, const double* scores, size_t length);
/* required so trained native detectors can be persisted and later published/evaluated */
AAS_EXPORT size_t aas_detector_state_size(void* instance);
AAS_EXPORT int    aas_detector_export_state(void* instance, unsigned char* out, size_t capacity);
AAS_EXPORT int    aas_detector_import_state(void* instance, const unsigned char* data, size_t length);

/* Metric kind */
AAS_EXPORT double aas_metric_compute(
    const int32_t* y_true, size_t y_true_len,
    const double* anomaly_score, size_t score_len,
    const int32_t* pred_label, size_t pred_len,
    const double* train_values, size_t train_len,
    const double* test_values, size_t test_len,
    const char* config_json,
    int* ok
);

#ifdef __cplusplus
}
#endif
#endif
