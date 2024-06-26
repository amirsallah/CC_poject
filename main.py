from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from kubernetes import client, config

config.load_kube_config()

app = FastAPI()


class AppConfig(BaseModel):
    app_name: str
    replicas: int
    image_address: str
    image_tag: str
    domain_address: str
    service_port: int
    resources: dict
    envs: dict
    secrets: dict = None
    external_access: bool = False


def create_secret(namespace, secret_name, secret_data):
    v1 = client.CoreV1Api()
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=secret_name),
        data=secret_data
    )
    v1.create_namespaced_secret(namespace=namespace, body=secret)


def create_deployment(namespace, app_name, replicas, image, tag, envs, resources, secret_name=None):
    apps_v1 = client.AppsV1Api()
    container = client.V1Container(
        name=app_name,
        image=f"{image}:{tag}",
        ports=[client.V1ContainerPort(container_port=80)],
        env=[client.V1EnvVar(name=key, value=value) for key, value in envs.items()],
        resources=client.V1ResourceRequirements(
            requests=resources
        )
    )
    if secret_name:
        container.env_from = [client.V1EnvFromSource(secret_ref=client.V1SecretEnvSource(name=secret_name))]

    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"app": app_name}),
        spec=client.V1PodSpec(containers=[container])
    )

    spec = client.V1DeploymentSpec(
        replicas=replicas,
        template=template,
        selector={'matchLabels': {'app': app_name}}
    )

    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=app_name),
        spec=spec
    )

    apps_v1.create_namespaced_deployment(
        namespace=namespace,
        body=deployment
    )


def create_service(namespace, app_name, service_port):
    v1 = client.CoreV1Api()
    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(name=app_name),
        spec=client.V1ServiceSpec(
            selector={"app": app_name},
            ports=[client.V1ServicePort(port=service_port, target_port=80)],
            type="ClusterIP"
        )
    )
    v1.create_namespaced_service(namespace=namespace, body=service)


def create_ingress(namespace, app_name, domain_address, service_port):
    networking_v1 = client.NetworkingV1Api()
    ingress = client.V1Ingress(
        api_version="networking.k8s.io/v1",
        kind="Ingress",
        metadata=client.V1ObjectMeta(name=app_name),
        spec=client.V1IngressSpec(
            rules=[
                client.V1IngressRule(
                    host=domain_address,
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            client.V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name=app_name,
                                        port=client.V1ServiceBackendPort(number=service_port)
                                    )
                                )
                            )
                        ]
                    )
                )
            ]
        )
    )
    networking_v1.create_namespaced_ingress(namespace=namespace, body=ingress)


@app.post("/deploy/")
def deploy_app(config: AppConfig):
    namespace = "default"
    try:
        if config.secrets:
            create_secret(namespace, config.app_name, config.secrets)
        create_deployment(
            namespace=namespace,
            app_name=config.app_name,
            replicas=config.replicas,
            image=config.image_address,
            tag=config.image_tag,
            envs=config.envs,
            resources=config.resources,
            secret_name=config.app_name if config.secrets else None
        )
        create_service(namespace, config.app_name, config.service_port)
        if config.external_access:
            create_ingress(namespace, config.app_name, config.domain_address, config.service_port)
        return {"status": "success", "message": f"Application {config.app_name} deployed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
