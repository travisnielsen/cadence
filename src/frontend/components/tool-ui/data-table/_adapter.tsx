/**
 * Adapter: UI and utility re-exports for copy-standalone portability.
 *
 * When copying this component to another project, update these imports
 * to match your project's paths:
 *
 *   cn           → Your Tailwind merge utility (e.g., "@/lib/utils", "~/lib/cn")
 *   Button       → shadcn/ui Button
 *   DropdownMenu → shadcn/ui DropdownMenu
 *   Accordion    → shadcn/ui Accordion
 *   Tooltip      → shadcn/ui Tooltip
 *   Badge        → shadcn/ui Badge
 *   Table        → shadcn/ui Table
 */

export { cn } from "@/lib/utils";
export {
    Accordion,
    AccordionContent,
    AccordionItem,
    AccordionTrigger
} from "../../ui/accordion";
export { Badge } from "../../ui/badge";
export { Button } from "../../ui/button";
export {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger
} from "../../ui/dropdown-menu";
export {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow
} from "../../ui/table";
export {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger
} from "../../ui/tooltip";

