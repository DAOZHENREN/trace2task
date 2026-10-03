package dev.trace2task.mobile;

/** Phone observation/execution stays independent of cloud vs future paired-PC planning. */
public interface ModelClient {
    ActionPlan predict(String task, String pngBase64, String history) throws Exception;
    void cancel();
}
