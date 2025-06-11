#!/usr/bin/env python3

import os
import subprocess
import requests
import json
import time
import glob
import sys
from typing import List, Optional

class HelmChartMigrator:
    def __init__(self, old_org: str, new_org: str, registry: str = "registry-1.docker.io"):
        self.old_org = old_org
        self.new_org = new_org
        self.registry = registry
        self.session = requests.Session()
        
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
        """Obtener todas las versiones disponibles de un chart"""
        print(f"  📋 Obteniendo versiones para {chart_name}...")
        
        # Método 1: Usar skopeo si está disponible
        versions = self._get_versions_with_skopeo(chart_name)
        if versions:
            return versions
            
        # Método 2: API de Docker Hub
        versions = self._get_versions_with_api(chart_name)
        if versions:
            return versions
            
        # Fallback: intentar con 'latest'
        print(f"  ⚠️  No se pudieron obtener versiones para {chart_name}, usando 'latest'")
        return ["latest"]
    
    def _get_versions_with_skopeo(self, chart_name: str) -> Optional[List[str]]:
        """Usar skopeo para obtener versiones"""
        try:
            cmd = ["skopeo", "list-tags", f"docker://{self.registry}/{self.old_org}/{chart_name}"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            return data.get("Tags", [])
        except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
            return None
    
    def _get_versions_with_api(self, chart_name: str) -> Optional[List[str]]:
        """Usar API de Docker Hub para obtener versiones"""
        try:
            url = f"https://registry.hub.docker.com/v2/repositories/{self.old_org}/{chart_name}/tags/"
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return [tag["name"] for tag in data.get("results", [])]
        except (requests.RequestException, json.JSONDecodeError, KeyError):
            pass
        return None
    
    def cleanup_local_files(self):
        """Limpiar archivos temporales locales"""
        patterns = ["*.tgz", "*.tar.gz"]
        for pattern in patterns:
            for file in glob.glob(pattern):
                try:
                    os.remove(file)
                    print(f"    🗑️  Eliminado {file}")
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
            return {"chart": chart, "total": 0, "success": 0, "status": "no_versions"}
        
        print(f"  📈 Encontradas {total_count} versiones")
        
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
    OLD_ORG = "jponton"
    NEW_ORG = "jpontoncharts"
    REGISTRY = "registry-1.docker.io"
    
    # Lista de charts a migrar
    CHARTS = [
        "chart1",
        "chart2", 
        "chart3"
        # Añade aquí tus charts reales
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
