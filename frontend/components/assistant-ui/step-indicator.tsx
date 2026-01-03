"use client";

import { memo, useState, useEffect, useRef, type FC } from "react";
import { Loader2, ChevronDown, ChevronRight, CheckCircle2 } from "lucide-react";
import { useMessage } from "@assistant-ui/react";

interface StepData {
  step: string;
  status?: "started" | "completed";
  duration_ms?: number;
  is_parent?: boolean;
}

/**
 * Format duration in a human-readable way
 */
function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * StepIndicator - Shows workflow steps in a collapsible hierarchical list
 * 
 * Parent steps (executor-level) contain child steps (tool-level).
 * Shows real-time progress with spinners and checkmarks with duration.
 */
const StepIndicatorImpl: FC = () => {
  const message = useMessage();
  const [isExpanded, setIsExpanded] = useState(true);
  const wasRunningRef = useRef(false);
  
  // Find the reasoning part (which contains our steps as JSON)
  const reasoningPart = message.content.find(
    (part) => part.type === "reasoning"
  );
  
  const isRunning = message.status?.type === "running";
  
  // Auto-collapse when message transitions from running to complete
  useEffect(() => {
    if (wasRunningRef.current && !isRunning) {
      setIsExpanded(false);
    }
    wasRunningRef.current = isRunning;
  }, [isRunning]);
  
  // Parse steps from reasoning part (it's a JSON array of StepData)
  let allSteps: StepData[] = [];
  if (reasoningPart?.type === "reasoning" && reasoningPart.text) {
    try {
      const parsed = JSON.parse(reasoningPart.text);
      if (Array.isArray(parsed)) {
        // Handle both old format (string[]) and new format (StepData[])
        allSteps = parsed.map(item => 
          typeof item === "string" 
            ? { step: item, status: "started" as const } 
            : item
        );
      }
    } catch {
      // Not JSON, might be legacy single step string
      allSteps = [{ step: reasoningPart.text, status: "started" }];
    }
  }
  
  if (allSteps.length === 0) {
    return null;
  }
  
  // Organize steps into parent/child hierarchy
  // Parent steps are executor-level, child steps are tool-level
  const parentSteps = allSteps.filter(s => s.is_parent);
  const childSteps = allSteps.filter(s => !s.is_parent);
  
  // Find the current active parent (last one that's started but not completed)
  // Or if all parents are completed, get the last completed one
  const activeParent = [...parentSteps].reverse().find(s => s.status === "started");
  const lastCompletedParent = [...parentSteps].reverse().find(s => s.status === "completed");
  const currentParent = activeParent || lastCompletedParent;
  
  // Calculate total duration from all completed parent steps
  const totalDuration = parentSteps
    .filter(s => s.status === "completed")
    .reduce((sum, s) => sum + (s.duration_ms || 0), 0) ||
    childSteps.filter(s => s.status === "completed")
      .reduce((sum, s) => sum + (s.duration_ms || 0), 0);
  
  // Find current in-progress child step for header display
  const currentChildStep = childSteps.find(s => s.status !== "completed");
  const allComplete = !activeParent && !currentChildStep;
  
  return (
    <div className="mb-3 rounded-lg border border-border/50 bg-muted/30 overflow-hidden">
      {/* Header - always visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 w-full px-3 py-2 text-sm text-muted-foreground hover:bg-muted/50 transition-colors"
      >
        {isExpanded ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        
        {allComplete ? (
          <>
            <CheckCircle2 className="h-4 w-4 text-green-500" />
            <span className="flex-1 text-left">
              Completed
              {totalDuration > 0 && (
                <span className="text-muted-foreground/70 ml-1">
                  ({formatDuration(totalDuration)})
                </span>
              )}
            </span>
          </>
        ) : (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="flex-1 text-left truncate">
              {currentParent?.step || currentChildStep?.step || "Processing..."}
            </span>
          </>
        )}
      </button>
      
      {/* Expandable step list - shows child steps */}
      {isExpanded && childSteps.length > 0 && (
        <div className="border-t border-border/50 px-3 py-2 space-y-1">
          {childSteps.filter(s => s.status === "completed").map((stepData, index) => (
            <div
              key={`${stepData.step}-${index}`}
              className="flex items-center gap-2 text-xs text-muted-foreground/70 pl-2"
            >
              <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
              <span className="truncate flex-1">{stepData.step}</span>
              {stepData.duration_ms !== undefined && (
                <span className="text-muted-foreground/50 tabular-nums">
                  {formatDuration(stepData.duration_ms)}
                </span>
              )}
            </div>
          ))}
          
          {childSteps.filter(s => s.status !== "completed").map((stepData, index) => (
            <div 
              key={`${stepData.step}-${index}`}
              className="flex items-center gap-2 text-xs text-foreground font-medium pl-2"
            >
              <Loader2 className="h-3 w-3 animate-spin shrink-0" />
              <span className="truncate">{stepData.step}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export const StepIndicator = memo(StepIndicatorImpl);
StepIndicator.displayName = "StepIndicator";
