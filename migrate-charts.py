#!/usr/bin/env python3

import os
import subprocess
import requests
import json
import time
import glob
import sys
import tempfile
import shutil
from typing import List, Optional

class HelmChartMigrator:
    def __init__(self, old_org: str, new_org: str, registry: str = "registry-1.docker.io"):
        self.old_org = old_org
        self.new_org = new_org
        self.registry = registry
        self.session = requests.Session()
        self.helm_version = self._get_helm_version()
        
    def _get_helm_version(self) -> str:
        """Get Helm version to determine available flags"""
        try:
            result = subprocess.run(["helm", "version", "--short"], 
                                  capture_output=True, text=True, check=True)
            version = result.stdout.strip()
            print(f"✓ Helm version: {version}")
            return version
        except:
            try:
                result = subprocess.run(["helm", "version"], 
                                      capture_output=True, text=True, check=True)
                version = result.stdout.strip().split('\n')[0]
                print(f"✓ Helm version: {version}")
                return version
            except:
                return "unknown"
        
    def check_dependencies(self) -> bool:
        """Verificar que helm esté instalado"""
        try:
            subprocess.run(["helm", "version"], capture_output=True, check=True)
            print("✓ Helm encontrado")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("❌ Error: helm no está instalado")
            return False
    
    def get_chart_versions(self, chart_name: str) -> List[str]:
        """Obtener todas las versiones disponibles de un chart que sean de tipo Helm"""
        print(f"  📋 Obteniendo versiones Helm para {chart_name}...")
        
        # Método 1: Usar skopeo si está disponible
        versions = self._get_helm_versions_with_skopeo(chart_name)
        if versions:
            return versions
            
        # Método 2: API de Docker Hub
        versions = self._get_helm_versions_with_api(chart_name)
        if versions:
            return versions
            
        # Fallback: intentar con 'latest' pero verificar si es Helm
        print(f"  ⚠️  No se pudieron obtener versiones para {chart_name}, verificando 'latest'...")
        if self._is_helm_chart(chart_name, "latest"):
            return ["latest"]
        else:
            print(f"  ❌ {chart_name}:latest no es un Helm chart")
            return []
    
    def _get_helm_versions_with_skopeo(self, chart_name: str) -> Optional[List[str]]:
        """Usar skopeo para listar todos los tags y devolver solo los que sean Helm charts."""
        try:
            # 1. Listar todos los tags del repositorio
            cmd_tags = [
                "skopeo", "list-tags",
                f"docker://{self.registry}/{self.old_org}/{chart_name}"
            ]
            print(f"[DEBUG] Ejecutando comando list-tags: {' '.join(cmd_tags)}")
            result_tags = subprocess.run(
                cmd_tags, capture_output=True, text=True, check=True
            )
            print(f"[DEBUG] Salida cruda de list-tags:\n{result_tags.stdout}")

            data_tags = json.loads(result_tags.stdout)
            all_tags = data_tags.get("Tags", [])
            print(f"[DEBUG] Tags parseados: {all_tags}")

            helm_versions = []

            # 2. Para cada tag, verificar si es un Helm chart
            for tag in all_tags:
                print(f"[DEBUG] Verificando tag: {tag}")
                if self._is_helm_chart(chart_name, tag):
                    print(f"[DEBUG] --> '{tag}' es un Helm chart, lo añado.")
                    helm_versions.append(tag)
                else:
                    print(f"[DEBUG] --> '{tag}' NO es un Helm chart, lo ignoro.")

            print(f"[DEBUG] Versiones Helm resultantes: {helm_versions}")
            return helm_versions

        except subprocess.CalledProcessError as e:
            print(f"[ERROR] fallo al listar tags para '{chart_name}': {e}")
            return None
        except FileNotFoundError:
            print("[ERROR] 'skopeo' no encontrado en el PATH")
            return None
        except json.JSONDecodeError as e:
            print(f"[ERROR] JSON inválido al listar tags: {e}")
            return None

    def _get_helm_versions_with_api(self, chart_name: str) -> Optional[List[str]]:
        """Usar API de Docker Hub para obtener versiones que sean Helm charts"""
        try:
            url = f"https://registry.hub.docker.com/v2/repositories/{self.old_org}/{chart_name}/tags/"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                helm_versions = []
                
                for tag_info in data.get("results", []):
                    tag_name = tag_info["name"]
                    
                    # Verificar si este tag específico es un Helm chart
                    if self._is_helm_chart(chart_name, tag_name):
                        helm_versions.append(tag_name)
                
                return helm_versions
        except (requests.RequestException, json.JSONDecodeError, KeyError):
            pass
        return None
    
    def _is_helm_chart(self, chart_name: str, version: str) -> bool:
        """Verificar si una versión específica es un Helm chart usando múltiples métodos"""
        print(f"[DEBUG] Verificando si {chart_name}:{version} es un Helm chart...")
        
        # Método 1: Verificación con helm pull a directorio temporal
        if self._verify_helm_with_pull_temp(chart_name, version):
            print(f"[DEBUG] {chart_name}:{version} verificado como Helm chart con 'helm pull' temporal")
            return True
        
        # Método 2: Verificar usando skopeo inspect con análisis mejorado
        if self._verify_helm_with_skopeo(chart_name, version):
            print(f"[DEBUG] {chart_name}:{version} verificado como Helm chart con 'skopeo inspect'")
            return True
        
        # Método 3: Verificar estructura típica de Helm chart OCI
        if self._verify_helm_oci_structure(chart_name, version):
            print(f"[DEBUG] {chart_name}:{version} verificado como Helm chart por estructura OCI")
            return True
        
        print(f"[DEBUG] {chart_name}:{version} NO es un Helm chart")
        return False
    
    def _verify_helm_with_pull_temp(self, chart_name: str, version: str) -> bool:
        """Verificar si es Helm chart intentando hacer pull a directorio temporal"""
        temp_dir = None
        try:
            print(f"[DEBUG] Intentando helm pull temporal para {chart_name}:{version}")
            
            # Crear directorio temporal
            temp_dir = tempfile.mkdtemp(prefix=f"helm-check-{chart_name}-")
            
            cmd = [
                "helm", "pull", 
                f"oci://{self.registry}/{self.old_org}/{chart_name}",
                "--version", version,
                "--destination", temp_dir
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            print(f"[DEBUG] helm pull temporal resultado - returncode: {result.returncode}")
            print(f"[DEBUG] helm pull temporal stdout: {result.stdout}")
            print(f"[DEBUG] helm pull temporal stderr: {result.stderr}")
            
            # Si helm pull funciona sin error, es un Helm chart
            if result.returncode == 0:
                # Verificar que se descargó un archivo .tgz
                tgz_files = glob.glob(os.path.join(temp_dir, "*.tgz"))
                if tgz_files:
                    print(f"[DEBUG] Archivo Helm chart descargado: {tgz_files[0]}")
                    return True
            
            # Verificar el error para confirmar si es por tipo de objeto
            error_msg = result.stderr.lower()
            if any(phrase in error_msg for phrase in [
                "not a helm chart", 
                "unsupported media type",
                "invalid chart",
                "not found",
                "no such manifest"
            ]):
                return False
            
            # Si hay otros errores (permisos, red, etc.), intentar otros métodos
            print(f"[DEBUG] Error ambiguo en helm pull temporal: {result.stderr}")
            return False
            
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"[DEBUG] Excepción en helm pull temporal: {e}")
            return False
        finally:
            # Limpiar directorio temporal
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except:
                    pass
    
    def _verify_helm_with_skopeo(self, chart_name: str, version: str) -> bool:
        """Verificar usando skopeo inspect con análisis mejorado"""
        try:
            cmd = ["skopeo", "inspect", f"docker://{self.registry}/{self.old_org}/{chart_name}:{version}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                print(f"[DEBUG] skopeo inspect falló: {result.stderr}")
                return False
                
            data = json.loads(result.stdout)
            print(f"[DEBUG] Analizando datos de skopeo inspect...")
            
            # 1. Verificar media type específico
            media_type = data.get("MediaType", "")
            if any(indicator in media_type.lower() for indicator in ["helm", "chart"]):
                print(f"[DEBUG] Detectado por MediaType: {media_type}")
                return True
            
            # 2. Verificar labels (incluyendo config.Labels)
            labels = data.get("Labels", {}) or {}
            config_labels = data.get("config", {}).get("Labels", {}) or {}
            all_labels = {**labels, **config_labels}
            
            if self._check_helm_labels(all_labels):
                return True
            
            # 3. Verificar estructura típica de OCI artifact
            layers = data.get("Layers", [])
            if len(layers) == 1:  # Helm charts OCI típicamente tienen un solo layer
                print(f"[DEBUG] Estructura compatible: un solo layer")
                return True
            
            # 4. Verificar por digest o características específicas
            manifest_digest = data.get("Digest", "")
            if manifest_digest and self._looks_like_helm_digest(data):
                return True
                
            return False
            
        except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            print(f"[DEBUG] Error en skopeo inspect: {e}")
            return False
    
    def _verify_helm_oci_structure(self, chart_name: str, version: str) -> bool:
        """Verificar estructura típica de Helm chart OCI usando características adicionales"""
        try:
            # Usar skopeo copy para verificar si se puede copiar como OCI artifact
            with tempfile.TemporaryDirectory() as temp_dir:
                cmd = [
                    "skopeo", "copy", "--dry-run",
                    f"docker://{self.registry}/{self.old_org}/{chart_name}:{version}",
                    f"oci:{temp_dir}/temp-{chart_name}-{version}"
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                # Si se puede copiar sin errores específicos de tipo, probablemente es válido
                if result.returncode == 0:
                    return True
                    
                # Verificar errores específicos
                error_msg = result.stderr.lower()
                if "unsupported" in error_msg or "invalid" in error_msg:
                    return False
                    
                return False
                
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    
    def _check_helm_labels(self, labels: dict) -> bool:
        """Verificar si los labels indican que es un Helm chart"""
        helm_indicators = [
            "org.opencontainers.image.title",
            "org.opencontainers.artifact.description", 
            "io.artifacthub.package.readme-url",
            "org.opencontainers.image.description",
            "io.artifacthub.package.maintainers",
            "helm.sh/chart"
        ]
        
        for label_key in helm_indicators:
            if label_key in labels:
                label_value = str(labels[label_key]).lower()
                if any(indicator in label_value for indicator in ["helm", "chart"]):
                    print(f"[DEBUG] Detectado por label {label_key}: {label_value}")
                    return True
        
        return False
    
    def _looks_like_helm_digest(self, data: dict) -> bool:
        """Verificar si las características del objeto sugieren que es un Helm chart"""
        # Verificar si tiene características típicas de un Helm chart OCI
        layers = data.get("Layers", [])
        
        # Helm charts OCI típicamente tienen:
        # - Un solo layer comprimido
        # - Tamaño relativamente pequeño para metadata
        if len(layers) == 1:
            # Si tenemos información de config, verificar más detalles
            config = data.get("config", {})
            if config and not config.get("Env") and not config.get("Cmd"):
                # Los Helm charts OCI no suelen tener ENV o CMD
                return True
        
        return False
    
    def cleanup_local_files(self):
        """Limpiar archivos temporales locales"""
        patterns = ["*.tgz", "*.tar.gz", "temp-*"]
        for pattern in patterns:
            for file in glob.glob(pattern):
                try:
                    if os.path.isfile(file):
                        os.remove(file)
                        print(f"    🗑️  Eliminado {file}")
                    elif os.path.isdir(file):
                        shutil.rmtree(file)
                        print(f"    🗑️  Eliminado directorio {file}")
                except OSError:
                    pass
    
    def migrate_chart_version(self, chart: str, version: str) -> bool:
        """Migrar una versión específica de un chart"""
        print(f"    📦 Migrando {chart}:{version}...")
        
        try:
            # Pull del chart
            pull_cmd = [
                "helm", "pull", 
                f"oci://{self.registry}/{self.old_org}/{chart}",
                "--version", version
            ]
            
            result = subprocess.run(pull_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"    ❌ Error al descargar {chart}:{version}")
                print(f"       {result.stderr.strip()}")
                return False
            
            print(f"    ✓ Descargado {chart}:{version}")
            
            # Encontrar el archivo descargado
            chart_files = glob.glob(f"{chart}-{version}.tgz") or glob.glob(f"{chart}-*.tgz")
            
            if not chart_files:
                print(f"    ❌ No se encontró archivo descargado para {chart}:{version}")
                return False
            
            chart_file = chart_files[0]
            
            # Push a la nueva organización
            push_cmd = [
                "helm", "push", chart_file,
                f"oci://{self.registry}/{self.new_org}/"
            ]
            
            result = subprocess.run(push_cmd, capture_output=True, text=True)
            
            # Limpiar archivo independientemente del resultado
            try:
                os.remove(chart_file)
            except OSError:
                pass
            
            if result.returncode != 0:
                print(f"    ❌ Error al subir {chart}:{version}")
                print(f"       {result.stderr.strip()}")
                return False
            
            print(f"    ✅ Subido {chart}:{version} exitosamente")
            return True
            
        except Exception as e:
            print(f"    ❌ Error inesperado con {chart}:{version}: {str(e)}")
            return False
    
    def migrate_chart(self, chart: str) -> dict:
        """Migrar todas las versiones de un chart"""
        print(f"\n📊 Procesando chart: {chart}")
        print("-" * 40)
        
        versions = self.get_chart_versions(chart)
        total_count = len(versions)
        success_count = 0
        
        if not versions:
            return {"chart": chart, "total": 0, "success": 0, "status": "no_helm_versions"}
        
        print(f"  📈 Encontradas {total_count} versiones de Helm charts")
        
        for i, version in enumerate(versions, 1):
            print(f"  [{i}/{total_count}] Procesando versión: {version}")
            
            if self.migrate_chart_version(chart, version):
                success_count += 1
            
            # Pausa para evitar rate limiting
            time.sleep(1)
        
        # Limpiar archivos restantes
        self.cleanup_local_files()
        
        # Determinar estado
        if success_count == total_count:
            status = "complete"
            status_icon = "✅"
        elif success_count > 0:
            status = "partial"
            status_icon = "⚠️"
        else:
            status = "failed"
            status_icon = "❌"
        
        print(f"  {status_icon} Resultado: {success_count}/{total_count} versiones migradas")
        
        return {
            "chart": chart,
            "total": total_count,
            "success": success_count,
            "status": status
        }
    
    def generate_verification_commands(self, charts: List[str]):
        """Generar comandos de verificación"""
        print("\n" + "=" * 50)
        print("🔍 COMANDOS DE VERIFICACIÓN")
        print("=" * 50)
        
        print("\nVerificar charts individuales:")
        for chart in charts:
            print(f"helm show chart oci://{self.registry}/{self.new_org}/{chart}")
        
        print("\nVer todas las versiones migradas:")
        for chart in charts:
            print(f"skopeo list-tags docker://{self.registry}/{self.new_org}/{chart}")
    
    def run_migration(self, charts: List[str]):
        """Ejecutar la migración completa"""
        print("🚀 MIGRACIÓN DE HELM CHARTS")
        print("=" * 50)
        print(f"Origen: {self.old_org}")
        print(f"Destino: {self.new_org}")
        print(f"Registry: {self.registry}")
        print(f"Charts a migrar: {len(charts)}")
        
        # Verificar dependencias
        if not self.check_dependencies():
            sys.exit(1)
        
        # Limpiar archivos previos
        self.cleanup_local_files()
        
        # Migrar cada chart
        results = []
        for chart in charts:
            result = self.migrate_chart(chart)
            results.append(result)
        
        # Resumen final
        self.print_summary(results)
        
        # Comandos de verificación
        self.generate_verification_commands(charts)
    
    def print_summary(self, results: List[dict]):
        """Imprimir resumen de la migración"""
        print("\n" + "=" * 50)
        print("📊 RESUMEN DE MIGRACIÓN")
        print("=" * 50)
        
        total_charts = len(results)
        complete_charts = sum(1 for r in results if r["status"] == "complete")
        partial_charts = sum(1 for r in results if r["status"] == "partial")
        failed_charts = sum(1 for r in results if r["status"] == "failed")
        
        total_versions = sum(r["total"] for r in results)
        success_versions = sum(r["success"] for r in results)
        
        print(f"📈 Charts procesados: {total_charts}")
        print(f"✅ Completamente migrados: {complete_charts}")
        print(f"⚠️  Parcialmente migrados: {partial_charts}")
        print(f"❌ Fallidos: {failed_charts}")
        print(f"📦 Total versiones: {total_versions}")
        print(f"✅ Versiones exitosas: {success_versions}")
        
        if success_versions > 0:
            success_rate = (success_versions / total_versions) * 100
            print(f"📊 Tasa de éxito: {success_rate:.1f}%")
        
        # Detalles por chart
        print("\nDetalle por chart:")
        for result in results:
            status_icons = {
                "complete": "✅",
                "partial": "⚠️",
                "failed": "❌",
                "no_versions": "❓"
            }
            icon = status_icons.get(result["status"], "❓")
            print(f"  {icon} {result['chart']}: {result['success']}/{result['total']}")


def main():
    # Configuración
    OLD_ORG = "gradiant"
    NEW_ORG = "gradiantcharts"
    REGISTRY = "registry-1.docker.io"
    
    # Lista de charts a migrar
    CHARTS = [
        "open5gs-upf",
        "open5gs-smf",
        "open5gs-bsf",
        "open5gs-nssf"
        
    ]
    
    # Crear migrador y ejecutar
    migrator = HelmChartMigrator(OLD_ORG, NEW_ORG, REGISTRY)
    
    try:
        migrator.run_migration(CHARTS)
    except KeyboardInterrupt:
        print("\n\n⚠️  Migración interrumpida por el usuario")
        migrator.cleanup_local_files()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error inesperado: {str(e)}")
        migrator.cleanup_local_files()
        sys.exit(1)


if __name__ == "__main__":
    main()