# Model weights as an ordinary OCI image, so a pod can mount them read-only
# as an image volume (GA since Kubernetes 1.36). containerd mounts only
# runnable images, not raw OCI artifacts (containerd#11381), hence an image.
FROM scratch
COPY model.gguf /model.gguf
