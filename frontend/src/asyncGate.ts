// 异步状态栅栏（加固计划 Task 10）：同一 key 的最新请求才允许落地。

export class LatestRequestGate {
  private epochs = new Map<string, number>()
  private controllers = new Map<string, AbortController>()

  begin(key: string): { epoch: number; signal: AbortSignal } {
    this.controllers.get(key)?.abort()
    const epoch = (this.epochs.get(key) ?? 0) + 1
    this.epochs.set(key, epoch)
    const controller = new AbortController()
    this.controllers.set(key, controller)
    return { epoch, signal: controller.signal }
  }

  isCurrent(key: string, epoch: number): boolean {
    return this.epochs.get(key) === epoch
  }

  cancel(key: string): void {
    this.controllers.get(key)?.abort()
    this.controllers.delete(key)
    this.epochs.delete(key)
  }

  cancelAll(): void {
    for (const key of [...this.controllers.keys()]) this.cancel(key)
  }
}
